"""Migration tools consume the public demiflow Lance API."""
import demiflow.lance.digest as platform_digest

from demiflow.lance.digest import (
    ROWHASH_V1, TABLE_DIGEST_V1, aggregate_digest_v1, rowhash_v1,
    table_digest_v1,
)


def test_reexports_are_the_platform_implementation():
    assert rowhash_v1 is platform_digest.rowhash_v1
    assert table_digest_v1 is platform_digest.table_digest_v1
    assert aggregate_digest_v1 is platform_digest.aggregate_digest_v1
    assert ROWHASH_V1 == platform_digest.ROWHASH_V1
    assert TABLE_DIGEST_V1 == platform_digest.TABLE_DIGEST_V1


def test_smoke_digest_still_computes():
    assert rowhash_v1({"a": 1}).startswith("rowhash-v1:")
    assert table_digest_v1([]).startswith("rowhash-v1-agg:")
