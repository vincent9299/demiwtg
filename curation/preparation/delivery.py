"""Read-only V4 delivery: published paragraphs plus independently scoped visuals."""
import argparse
from collections import defaultdict
from pathlib import Path


from curation.preparation.records import ROOT, digest, file_record, immutable, local_data, read, rows, run_lock


def accepted(review):
    return (isinstance(review, dict) and review.get("decision") == "accept"
            and review.get("reviewer_kind") in {"assistant", "human", "independent"}
            and all(review.get(k) for k in ("reviewer", "reason", "evidence", "scope")))


def asset_review_ok(review, role):
    if (role == 'reference' and review.get('publisher') in {'PublishArticle', 'PublishVisualMaterials'}
            and review.get('publication_status') == 'reviewed' and review.get('evidence')
            and review.get('support_scope')):
        return True
    if not accepted(review):
        return False
    if role in {"reference", "edit_source"}:
        # Unknown production history needs no certificate. Preserve explicit
        # negative findings in legacy reviews; acceptance does not certify origin.
        return (review.get("non_generated") is not False and review.get("identity_checked") is True
                and bool(review.get("support_scope")))
    return True


def attach_identity(item):
    fingerprint = digest(item)
    return {**item, "item_id": ("K" if item["kind"] == "text" else "V") + fingerprint[:24],
            "fingerprint": fingerprint, "review": {"decision": "pending"}}


def source_entries(run):
    """Read final publication; intermediate pools only resolve cited source bytes."""
    from curation.preparation.published import published_record
    from curation.preparation.publication_sources import iter_publication_rows
    records, identity = iter_publication_rows(run)
    for row in records:
        delivered = published_record({**row, '_knowledge_run': identity.get('run'),
                                      '_knowledge_sha256': digest(row), '_article_source': identity})
        yield from delivered['materials']


def apply_reviews(items, reviews):
    by_id = {item["item_id"]: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("Duplicate knowledge item")
    for review in reviews:
        item = by_id.get(review.get("item_id"))
        if item is None or review.get("fingerprint") != item["fingerprint"]:
            raise ValueError("Review is not bound to this exact knowledge item")
        if item["review"].get("decision") != "pending":
            raise ValueError("Duplicate review")
        if accepted(review):
            if item["kind"] == "text" and (not item["references"] or not item["sources"]):
                raise ValueError("Accepted paragraph must retain its cited source context")
            if item["kind"] == "image" and item["upstream_status"] != "keep_candidate":
                raise ValueError("Pending/excluded upstream image cannot be promoted downstream")
        item["review"] = review
    return items


def export_catalog(sources, output, reviews=None):
    """Publish the final knowledge delivery directly as a Lance stage."""
    if reviews is not None:
        raise ValueError('Final publication cannot be bypassed by downstream review sidecars')
    from curation.preparation.publication_sources import freeze_publication_source
    from curation.preparation.records import run_records
    from curation.preparation.stages import EncodeStage, stage_uri, stage_ref
    from curation.preparation.schemas import PIPELINE_STAGE_ROWS
    frozen = [freeze_publication_source(source) for source in sources]
    manifest = {'schema':'v2-knowledge-delivery/1','sources':frozen,
                'adapter_sha256':digest(Path(__file__).read_bytes())}
    version = digest(manifest)
    run_records(output).put('manifest',manifest)
    (local_data().from_iter(lambda: (item for source in frozen for item in source_entries(source)))
        .map(lambda item: {**item,'knowledge_version':version}).map(EncodeStage('catalog'))
        .checkpoint_lance(stage_uri(output,'catalog'),schema=PIPELINE_STAGE_ROWS,fingerprint=version).take_all())
    return stage_ref(output,'catalog').to_dict()


def eligible(item):
    if item.get('publication', {}).get('adapter') == 'visual-publication/1':
        return (item['publication'].get('status') == 'published' and item['kind']=='image'
                and bool(item.get('asset')) and asset_review_ok(item.get('review', {}), 'reference'))
    if item.get('publication', {}).get('adapter') == 'final-publication/2':
        return (item['publication'].get('status') == 'published'
                and item.get('review', {}).get('publisher') == 'PublishArticle'
                and bool(item.get('references') if item['kind'] == 'text' else item.get('asset')))
    if item["kind"] == "text":
        return accepted(item["review"]) and bool(item["references"] and item["sources"])
    return item["upstream_status"] == "keep_candidate" and asset_review_ok(item["review"], "reference")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    print(export_catalog(args.runs, args.output, args.reviews))


if __name__ == "__main__":
    main()
