import scenario


def test_patch():
    relation = scenario.Relation("foo", local_app_data={"foo": "bar"})
    state = scenario.State(relations=[relation])

    patched = state.patch(relation, local_app_data={"baz": "qux"})
    assert list(patched.relations)[0].local_app_data == {"baz": "qux"}


def test_remap():
    relation = scenario.Relation("foo", local_app_data={"foo": "bar"})
    state = scenario.State(relations=[relation])
    relation_out = state.remap(relation)
    # in this case we didn't change it
    assert relation_out is relation


def test_insert():
    relation = scenario.Relation("foo", local_app_data={"foo": "bar"})
    state = scenario.State().insert(relation)
    assert state.relations == {relation}


def test_insert_multiple():
    relation = scenario.Relation("foo", local_app_data={"foo": "bar"})
    relation2 = scenario.Relation("foo", local_app_data={"buz": "fuz"})

    state = scenario.State().insert(relation, relation2)

    assert state.relations == {relation2, relation}


def test_without():
    relation = scenario.Relation("foo", local_app_data={"foo": "bar"})
    relation2 = scenario.Relation("foo", local_app_data={"buz": "fuz"})

    state = scenario.State(relations=[relation, relation2]).without(relation)
    assert list(state.relations) == [relation2]


def test_without_multiple():
    relation = scenario.Relation("foo", local_app_data={"foo": "bar"})
    relation2 = scenario.Relation("foo", local_app_data={"buz": "fuz"})

    state = scenario.State(relations=[relation, relation2]).without(relation, relation2)
    assert list(state.relations) == []


def test_insert_replace():
    relation1 = scenario.Relation("foo", local_app_data={"foo": "bar"}, id=1)
    relation2 = scenario.Relation("foo", local_app_data={"buz": "fuz"}, id=2)

    relation1_dupe = scenario.Relation("foo", local_app_data={"noz": "soz"}, id=1)

    state = scenario.State(relations=[relation1, relation2]).insert(relation1_dupe)

    # inserting a relation with identical ID will kick out the old one
    assert set(state.relations) == {relation2, relation1_dupe}
