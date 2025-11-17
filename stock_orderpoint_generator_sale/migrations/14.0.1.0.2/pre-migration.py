from openupgradelib import openupgrade


def migrate(cr, installed_version):
    # set default false to orderpoint_generate_active
    query = """
    UPDATE product_template
    SET orderpoint_generate_active = false
    """
    return openupgrade.logged_query(cr, query)
