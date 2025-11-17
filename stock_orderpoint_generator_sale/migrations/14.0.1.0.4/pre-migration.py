from openupgradelib import openupgrade


def migrate(cr, installed_version):
    # set default false to orderpoint_generate_active
    query = """
    UPDATE product_template
    SET orderpoint_generate_active = false
    """
    openupgrade.logged_query(cr, query)
    # set true to product with orderpoint with values
    query = """
    UPDATE product_template
    SET orderpoint_generate_active = true
    WHERE id IN (
        SELECT pt.id FROM product_template pt
        LEFT JOIN product_product pp
        ON pp.product_tmpl_id = pt.id
        LEFT JOIN stock_warehouse_orderpoint swo
        ON swo.product_id = pp.id
        WHERE swo.active is true
        AND swo.product_max_qty != 0
        AND swo.product_min_qty != 0
    )
    """
    openupgrade.logged_query(cr, query)
