"""Rewrite the Jinja-only filters left in mail templates into QWeb expressions.

From Odoo 16.0 the mail bodies are rendered by QWeb only: ``mail.template``
``body_html``/``subject``, ``mailing.mailing`` ``body_arch``/``body_html``/
``preview``.  A body migrated from 15.0 or less keeps Jinja filters and tests --
``|selectattr('type', 'equalto', 'pet')``, ``|map(attribute='name')``,
``|join(', ')``, ``|length`` -- and QWeb resolves those names through its
``values`` namespace, so the template raises at render time
(``TypeError: 'NoneType' object is not callable``) instead of being sent.  A
leftover ``${expr}`` from the same engine is even quieter: it is printed
verbatim in the email, so it is only reported here.

This module has no Odoo import: ``openupgrader.migration`` feeds its source to
``odoo shell`` on the migrated database of the target version, so the bodies
are written with the ORM of the version that renders them (translated ``Html``
fields are ``jsonb``: a SQL update would have to rebuild every language key and
would bypass the field sanitizer).  It can also be run by hand, see the
``main()`` function.

The originals are saved (when ``OPENUPGRADER_MAIL_TEMPLATE_DUMP`` is set) as one
``.html`` file per value plus an ``index.json``; that directory is the
``OUT`` of ``/workspace/tools/fix_mail_templates_qweb.py MODE=restore``, which
puts the bodies back.

Only the value of the ``t-*`` attributes is rewritten, one expression at a
time; the rest of the markup is never touched.  Filters that are not listed in
``apply_filter`` are reported and skipped, never guessed.
"""

import html as html_lib
import json
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

# model, field pairs the QWeb engine renders
TARGETS = (
    ("mail.template", "body_html"),
    ("mail.template", "subject"),
    ("mailing.mailing", "body_arch"),
    ("mailing.mailing", "body_html"),
    ("mailing.mailing", "preview"),
)

SUSPECT = re.compile(
    r"selectattr|rejectattr|map\(\s*attribute|\|\s*(join|length|count|sort|sum|"
    r"round|first|last|default|int|float|string|str|title|upper|lower|capitalize|"
    r"trim|unique|safe|striptags|wordwrap|truncate|replace|format|to_json)\b"
)
LEFTOVER = re.compile(r"\$\{")

# one line per converted/skipped value, parsed by the migration button
RESULT_PREFIX = "MAIL-QWEB-FIX"
SUMMARY_LINE = (
    RESULT_PREFIX + " | summary | converted=%(converted)d skipped=%(skipped)d "
    "leftover=%(leftover)d errors=%(errors)d"
)


class Unsupported(Exception):
    """A Jinja construct this module does not know how to rewrite."""


# --------------------------------------------------------------------------- #
# expression rewriting
# --------------------------------------------------------------------------- #
def _scan(text):
    """Return ``(depth, in_string)`` for every character of ``text``."""
    out = []
    depth = 0
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            out.append((depth, True))
            if char == "\\" and index + 1 < len(text):
                out.append((depth, True))
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            out.append((depth, False))
        elif char in "([{":
            out.append((depth, False))
            depth += 1
        elif char in ")]}":
            depth -= 1
            out.append((depth, False))
        else:
            out.append((depth, False))
        index += 1
    return out


def split_top(text, separator):
    """Split on ``separator`` only at bracket depth 0 and outside strings."""
    info = _scan(text)
    starts = []
    index = 0
    while index <= len(text) - len(separator):
        if text.startswith(separator, index):
            depth, in_string = info[index]
            if depth == 0 and not in_string:
                starts.append(index)
                index += len(separator)
                continue
        index += 1
    if not starts:
        return [text]
    parts = []
    previous = 0
    for position in starts:
        parts.append(text[previous:position])
        previous = position + len(separator)
    parts.append(text[previous:])
    return parts


def _wrap(text):
    """Parenthesise an operand only when it is a compound expression."""
    text = text.strip()
    for separator in (" or ", " and ", ","):
        if len(split_top(text, separator)) > 1:
            return "(%s)" % text
    return text


def _field_expr(base, field):
    field = field.strip()
    if len(field) > 1 and field[0] in "\"'" and field[-1] == field[0]:
        name = field[1:-1]
        if re.match(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$", name):
            return "%s.%s" % (base, name)
        return "getattr(%s, %r)" % (base, name)
    return "getattr(%s, %s)" % (base, field)


TESTS = {
    "equalto": "==",
    "eq": "==",
    "ne": "!=",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "sameas": "is",
}


def _predicate(args, kwargs):
    """Build the lambda body of a ``selectattr``/``rejectattr`` filter."""
    if not args:
        raise Unsupported("selectattr without attribute")
    left = _field_expr("_r", kwargs.get("attribute", args[0]))
    if len(args) == 1 and "attribute" not in kwargs:
        return "bool(%s)" % left
    rest = args[1:]
    test = rest[0].strip().strip("\"'") if rest else kwargs.get("test", "").strip("\"'")
    value = rest[1] if len(rest) > 1 else kwargs.get("value")
    if test in ("none",):
        return "%s is None" % left
    if not test:
        return "bool(%s)" % left
    if test and value is None:
        raise Unsupported("selectattr test without value: %s" % test)
    if test in TESTS:
        return "%s %s %s" % (left, TESTS[test], value)
    if test == "in":
        return "(%s) in (%s)" % (left, value)
    if test in ("truthy", "boolean"):
        return "bool(%s)" % left
    raise Unsupported("selectattr test %r" % test)


def apply_filter(base, name, args, kwargs):  # noqa: C901 - one branch per filter
    """Return the python expression replacing ``base|name(*args, **kwargs)``."""
    wrapped = _wrap(base)
    if name == "selectattr":
        return "%s.filtered(lambda _r: %s)" % (wrapped, _predicate(args, kwargs))
    if name == "rejectattr":
        return "%s.filtered(lambda _r: not (%s))" % (wrapped, _predicate(args, kwargs))
    if name == "map":
        attribute = kwargs.get("attribute", args[0] if args else None)
        if attribute is None:
            raise Unsupported("map without attribute")
        attribute = attribute.strip().strip("\"'")
        if not re.match(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$", attribute):
            raise Unsupported("map attribute %r" % attribute)
        return "%s.mapped(%r)" % (wrapped, attribute)
    if name == "join":
        separator = args[0] if args else "''"
        return "%s.join(%s)" % (separator, base.strip())
    if name in ("length", "count"):
        return "len(%s)" % wrapped
    if name == "first":
        return "(%(b)s)[0] if (%(b)s) else ''" % {"b": wrapped}
    if name == "last":
        return "(%(b)s)[-1] if (%(b)s) else ''" % {"b": wrapped}
    if name == "default":
        if len(args) != 1:
            raise Unsupported("default with %d arguments" % len(args))
        return "(%s or (%s))" % (wrapped, args[0])
    if name in ("int", "float", "str", "string"):
        return "%s(%s)" % ("str" if name in ("str", "string") else name, wrapped)
    if name == "round":
        return "round(%s%s)" % (wrapped, (", " + ", ".join(args)) if args else "")
    if name == "sum":
        attribute = kwargs.get("attribute", args[0] if args else None)
        if attribute:
            return "sum(%s.mapped(%r))" % (wrapped, attribute.strip().strip("\"'"))
        return "sum(%s)" % wrapped
    if name == "sort":
        reverse = (
            "reverse=True"
            if str(kwargs.get("reverse", "false")).lower() == "true"
            else ""
        )
        attribute = kwargs.get("attribute")
        if attribute:
            attribute = attribute.strip().strip("\"'")
            return "%s.sorted(key=lambda _r: _r.%s%s)" % (
                wrapped,
                attribute,
                (", " + reverse) if reverse else "",
            )
        if reverse:
            return "sorted(%s, %s)" % (wrapped, reverse)
        return "sorted(%s)" % wrapped
    if name == "abs":
        return "abs(%s)" % wrapped
    if name == "trim":
        return "%s.strip()" % wrapped
    if name in ("upper", "lower", "capitalize", "title"):
        return "%s.%s()" % (wrapped, name)
    raise Unsupported("filter %r" % name)


def parse_filter(segment):
    """Split ``name(arg, key=value)`` into ``(name, args, kwargs)``."""
    match = re.match(r"^\s*([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*$", segment, re.DOTALL)
    if not match:
        raise Unsupported("filter segment %r" % segment.strip())
    name, raw = match.group(1), match.group(2)
    args, kwargs = [], {}
    if raw:
        for argument in [a.strip() for a in split_top(raw, ",") if a.strip()]:
            key_value = split_top(argument, "=")
            if len(key_value) == 2 and re.match(r"^\w+$", key_value[0].strip()):
                kwargs[key_value[0].strip()] = key_value[1].strip()
            else:
                args.append(argument)
    return name, args, kwargs


def convert_chain(expression):
    segments = split_top(expression, "|")
    if len(segments) == 1:
        return expression.strip()
    base = segments[0].strip()
    for segment in segments[1:]:
        name, args, kwargs = parse_filter(segment)
        base = apply_filter(base, name, args, kwargs)
    return base


def convert_expr(expression):
    """Convert a Jinja-filter expression into a QWeb (python) expression."""
    for operator in (" or ", " and "):
        parts = split_top(expression, operator)
        if len(parts) > 1:
            return operator.join(convert_expr(part) for part in parts)
    for operator in (" not in ", " in ", " == ", " != ", " <= ", " >= ", " < ", " > "):
        parts = split_top(expression, operator)
        if len(parts) > 1:
            return operator.join(convert_chain(part) for part in parts)
    return convert_chain(expression)


ATTR_RE = re.compile(
    r"(\bt-[A-Za-z][\w-]*(?:-[\w-]+)*\s*=\s*\")([^\"]*)(\")", re.DOTALL
)


def convert_html(value):
    """Rewrite the ``t-*`` expressions of ``value``.

    :return: ``(new_value, [(old_expression, new_expression), ...])``
    :raises Unsupported: when one of the expressions uses an unknown filter;
        the value is then left untouched by the caller.
    """
    changes = []

    def replace(match):
        old = match.group(2)
        if not SUSPECT.search(old):
            return match.group(0)
        # attribute values are entity encoded (``&gt;``, ``&amp;``): work on the
        # decoded expression and encode the result the same way
        new = convert_expr(html_lib.unescape(old))
        new = (
            new.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        if new == old:
            return match.group(0)
        changes.append((old, new))
        return match.group(1) + new + match.group(3)

    return ATTR_RE.sub(replace, value), changes


# --------------------------------------------------------------------------- #
# database access
# --------------------------------------------------------------------------- #
def targets(env):
    """Return the ``(model, field)`` pairs installed in ``env``."""
    return [
        (model, field)
        for model, field in TARGETS
        if model in env and field in env[model]._fields
    ]


def read_values(env, model, field, record_id):
    """Return ``{language or None: value}`` reading the column as stored.

    Reading the raw column keeps every language of a translated field visible;
    ``env.model.read()`` would only return the one of the context.
    """
    query = 'SELECT "%s" FROM "%s" WHERE id = %%s' % (field, env[model]._table)
    env.cr.execute(query, (record_id,))
    value = env.cr.fetchone()[0]
    if isinstance(value, dict):
        return value
    return {None: value}


def label(record):
    for field_name in ("name", "subject"):
        if field_name in record._fields:
            value = record[field_name]
            if value:
                return value if isinstance(value, str) else str(value)
    return ""


def _save_original(dump_dir, model, field, record_id, language, value, name):
    """Write one original value under ``dump_dir`` and return its file name."""
    if not dump_dir:
        return None
    os.makedirs(dump_dir, exist_ok=True)
    file_name = "%s_%s_%s_%s_%s.html" % (
        model.replace(".", "_"),
        record_id,
        field,
        language or "raw",
        re.sub(r"\W+", "_", name or "")[:40],
    )
    with open(os.path.join(dump_dir, file_name), "w") as original:
        original.write(value)
    return file_name


def fix(env, dump_dir=None):
    """Convert the templates of ``env``; return the report lines and counters."""
    # the raw SELECT of read_values() must see the records created or written
    # in the current transaction (``Environment.flush_all`` exists from 15.0 on)
    flush_all = getattr(env, "flush_all", None)
    if flush_all:
        flush_all()
    else:
        for model, _field in targets(env):
            env[model].flush()
    lines = []
    stats = {"converted": 0, "skipped": 0, "leftover": 0, "errors": 0}
    index = []
    for model, field in targets(env):
        records = env[model].search([])
        for record in records:
            for language, value in read_values(env, model, field, record.id).items():
                if not value:
                    continue
                has_filter = bool(SUSPECT.search(value))
                has_leftover = bool(LEFTOVER.search(value))
                if not has_filter and not has_leftover:
                    continue
                key = "%s | %s | %s | %s | %s |" % (
                    model,
                    record.id,
                    field,
                    language,
                    label(record),
                )
                if has_leftover and not has_filter:
                    stats["leftover"] += 1
                    lines.append(
                        RESULT_PREFIX + " | leftover | %s a leftover ${...} must be "
                        "converted by hand" % key
                    )
                    continue
                try:
                    new_value, changes = convert_html(value)
                except Unsupported as error:
                    stats["skipped"] += 1
                    lines.append(RESULT_PREFIX + " | skipped | %s %s" % (key, error))
                    continue
                if not changes:
                    continue
                file_name = _save_original(
                    dump_dir, model, field, record.id, language, value, label(record)
                )
                if file_name:
                    index.append(
                        {
                            "file": file_name,
                            "model": model,
                            "field": field,
                            "id": record.id,
                            "lang": language,
                            "name": label(record),
                        }
                    )
                target = record
                if language:
                    target = target.with_context(lang=language)
                try:
                    target.write({field: new_value})
                except Exception as error:  # noqa: BLE001 - reported, not fatal
                    stats["errors"] += 1
                    lines.append(
                        RESULT_PREFIX
                        + " | error | %s %s: %s" % (key, type(error).__name__, error)
                    )
                    continue
                stats["converted"] += 1
                lines.append(
                    RESULT_PREFIX
                    + " | converted | %s %d expression(s)" % (key, len(changes))
                )
                for old_expression, new_expression in changes:
                    lines.append("    - %s" % old_expression)
                    lines.append("    + %s" % new_expression)
    if dump_dir and index:
        with open(os.path.join(dump_dir, "index.json"), "w") as index_file:
            json.dump(index, index_file, indent=2, ensure_ascii=False)
        lines.append(
            RESULT_PREFIX
            + " | backup | %d original value(s) in %s" % (len(index), dump_dir)
        )
    return stats, lines


def main(env, dump_dir=None):
    """Entry point used by ``odoo shell`` (see the module docstring)."""
    if dump_dir is None:
        dump_dir = os.environ.get("OPENUPGRADER_MAIL_TEMPLATE_DUMP")
    stats, lines = fix(env, dump_dir)
    # the caller reads stdout: print() is reported by pylint-odoo
    for line in lines:
        sys.stdout.write("%s\n" % line)
    sys.stdout.write("%s\n" % (SUMMARY_LINE % stats))
    return stats
