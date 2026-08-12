# -*- coding: utf-8 -*-
"""SQLGlot guard 的属性测试。"""
from hypothesis import given, strategies as st
import pytest

from src.connectors.sql_guard import SqlGuardError, validate_select


@given(st.sampled_from(["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "PRAGMA", "ATTACH"]))
def test_non_select_statements_never_pass(keyword):
    sql = f"{keyword} safe_table"
    with pytest.raises(SqlGuardError):
        validate_select(sql, allowed_tables={"safe_table"}, dialect="sqlite")


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=30))
def test_unlisted_table_names_never_pass(table_name):
    if not table_name.replace("_", "a").isalnum() or table_name[0].isdigit():
        return
    with pytest.raises(SqlGuardError):
        validate_select(f'SELECT * FROM "{table_name}"', allowed_tables={"allowed"}, dialect="sqlite")
