from src import config, r2_storage


def test_is_configured_requires_all_four_vars(monkeypatch):
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(config, "GENERATED_SITES_BASE_URL", "https://sites.example.com")
    assert r2_storage.is_configured() is False

    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "acct")
    assert r2_storage.is_configured() is True


def test_public_url_joins_base_and_key_cleanly(monkeypatch):
    monkeypatch.setattr(config, "GENERATED_SITES_BASE_URL", "https://sites.example.com/")
    assert r2_storage.public_url("sites/abc123/preschool-warm/index.html") == (
        "https://sites.example.com/sites/abc123/preschool-warm/index.html"
    )
    assert r2_storage.public_url("/sites/abc123/photos/0.jpg") == (
        "https://sites.example.com/sites/abc123/photos/0.jpg"
    )


def test_upload_bytes_raises_clear_error_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(r2_storage, "_client", None)

    import pytest
    with pytest.raises(r2_storage.R2NotConfiguredError):
        r2_storage.upload_bytes("sites/x/index.html", b"<html></html>", "text/html")


class _FakeS3Client:
    """Stands in for boto3's S3 client, paginating list_objects_v2 in pages
    of 2 (key-based continuation, like real S3) so delete_prefix's
    ContinuationToken loop actually gets exercised. Listing is computed from
    the original key set, not whatever's left after deletes — same as real
    S3, where a continuation token names a key, not a position, so deleting
    earlier pages doesn't perturb later ones."""

    def __init__(self, keys):
        self._all_keys = list(keys)
        self.deleted = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        matching = sorted(k for k in self._all_keys if k.startswith(Prefix))
        page_size = 2
        idx = matching.index(ContinuationToken) + 1 if ContinuationToken else 0
        page = matching[idx:idx + page_size]
        truncated = idx + page_size < len(matching)
        resp = {"Contents": [{"Key": k} for k in page], "IsTruncated": truncated}
        if truncated:
            resp["NextContinuationToken"] = page[-1]
        return resp

    def delete_objects(self, Bucket, Delete):
        self.deleted.extend(obj["Key"] for obj in Delete["Objects"])


def test_delete_prefix_deletes_only_matching_keys_and_paginates(monkeypatch):
    fake_client = _FakeS3Client([
        "sites/abc123/index.html",
        "sites/abc123/photos/0.jpg",
        "sites/abc123/photos/1.jpg",
        "sites/other456/index.html",
    ])
    monkeypatch.setattr(r2_storage, "_get_client", lambda: fake_client)

    deleted_count = r2_storage.delete_prefix("sites/abc123/")

    assert deleted_count == 3
    assert sorted(fake_client.deleted) == [
        "sites/abc123/index.html",
        "sites/abc123/photos/0.jpg",
        "sites/abc123/photos/1.jpg",
    ]
    assert "sites/other456/index.html" not in fake_client.deleted


def test_delete_prefix_is_a_no_op_when_nothing_matches(monkeypatch):
    fake_client = _FakeS3Client(["sites/other456/index.html"])
    monkeypatch.setattr(r2_storage, "_get_client", lambda: fake_client)

    assert r2_storage.delete_prefix("sites/does-not-exist/") == 0
    assert fake_client.deleted == []
