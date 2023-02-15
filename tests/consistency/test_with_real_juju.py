import pytest

from tests.consistency.conftest import check_pass, check_fail


@pytest.mark.parametrize('relation_name', ("secret_id", "kabozz"))
def test_relation_ids(relation_name):
    check_pass('relation_ids', relation_name)


def test_relation_ids_pass():
    check_pass('relation_list', 1)


def test_relation_ids_fail():
    check_fail('relation_list', 4242)

