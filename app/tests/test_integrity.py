"""Integrity guards.

These tests fail the build if the codebase acquires the failure modes the
product documents forbid: fixture fallbacks, invented SDK usage, an LLM in the
measurement path, or evidence without provenance.
"""

import ast
import inspect
from pathlib import Path

import pytest

import videodb
from adproof import states
from adproof.evaluation import evaluators, intervals
from adproof.models import EvidenceItem
from adproof.providers import videodb_adapter

SRC = Path(__file__).resolve().parent.parent / "src" / "adproof"


def _python_files():
    return [p for p in SRC.rglob("*.py")]


# -- no fixture fallback ---------------------------------------------------


def test_evidence_origin_has_no_fixture_member():
    """A fixture origin cannot exist, so fixture evidence cannot be recorded."""
    assert [m.value for m in states.EvidenceOrigin] == ["live_provider"]


def test_no_fixture_or_mock_modules_in_source():
    banned = ("fixture", "mock", "sample_data", "demo_data", "fake")
    offenders = [
        p.name
        for p in _python_files()
        if any(token in p.name.lower() for token in banned)
    ]
    assert not offenders, f"fixture-like modules in the live path: {offenders}"


def test_adapter_never_fabricates_data_in_an_except_block():
    """An except block that returns DATA is how silent fallbacks are born.

    Two narrowly-justified returns are permitted, both of which report a real
    provider state rather than substituting invented data:

    * `CreatedIndex(...)` when the provider says an index already exists;
    * an EMPTY LIST literal, because VideoDB signals "no search results" by
      raising rather than returning. An empty list carries zero information, so
      by construction it cannot fabricate evidence -- and the caller still has
      to classify the absence under the rule's policy.

    Anything else -- any non-empty literal, any other call, any variable --
    remains banned.
    """
    source = inspect.getsource(videodb_adapter)
    tree = ast.parse(source)
    offenders = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            value = node.value
            if (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", "") == "CreatedIndex"
            ):
                continue
            if isinstance(value, ast.List) and not value.elts:
                continue
            offenders.append(ast.dump(value)[:100])
    assert not offenders, f"adapter returns data from except blocks: {offenders}"


def test_empty_search_result_is_not_reported_as_a_provider_failure():
    """VideoDB raises InvalidRequestError('No results found.') for an empty hit set.

    Verified against the live API 2026-07-26. Mapping that to a provider
    failure would report a legitimate absence as an error, destroying the
    distinction between "we looked and found nothing" and "we never looked".
    """
    from videodb.exceptions import InvalidRequestError

    from adproof.providers.videodb_adapter import _EMPTY_RESULT_MARKERS

    message = str(InvalidRequestError("Invalid request: No results found. ")).lower()
    assert any(marker in message for marker in _EMPTY_RESULT_MARKERS)


# -- no invented SDK usage -------------------------------------------------


def test_only_the_adapter_imports_the_provider_sdk():
    offenders = []
    for path in _python_files():
        if path.name == "videodb_adapter.py":
            continue
        text = path.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import videodb", "from videodb")):
                # retrieval/plan.py imports only the IndexType/SearchType
                # constant enums, which are values, not provider calls.
                if "import IndexType" in stripped or "IndexType, SearchType" in stripped:
                    continue
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, f"SDK leaked outside the adapter: {offenders}"


@pytest.mark.parametrize(
    "method",
    [
        "upload",
        "get_video",
        "get_collection",
        "index_spoken_words",
        "index_scenes",
        "legacy_search",
        "generate_stream",
    ],
)
def test_every_sdk_method_used_actually_exists(method):
    """Guards against invented method names surviving a version bump."""
    targets = (
        videodb.video.Video,
        videodb.collection.Collection,
        videodb.client.Connection,
    )
    assert any(hasattr(t, method) for t in targets), (
        f"{method!r} does not exist on the pinned videodb SDK"
    )


@pytest.mark.parametrize(
    "enum_path,member",
    [
        ("IndexType", "spoken_word"),
        ("IndexType", "scene"),
        ("SearchType", "semantic"),
        ("SearchType", "keyword"),
        ("SceneExtractionType", "time_based"),
    ],
)
def test_every_sdk_constant_used_actually_exists(enum_path, member):
    assert hasattr(getattr(videodb, enum_path), member)


def test_shot_exposes_every_field_the_adapter_reads():
    fields = inspect.signature(videodb.shot.Shot.__init__).parameters
    for name in (
        "start",
        "end",
        "text",
        "search_score",
        "scene_index_id",
        "scene_index_name",
        "stream_url",
    ):
        assert name in fields, f"Shot has no {name!r} in the pinned SDK"


# -- deterministic measurement stays deterministic -------------------------


@pytest.mark.parametrize("module", [intervals, evaluators])
def test_measurement_modules_perform_no_io_and_call_no_model(module):
    """Inspect identifiers, not prose.

    Checking raw source text would flag a docstring that merely names VideoDB,
    which proves nothing. What matters is whether the code can reach a network
    or a model, so walk the AST for actual names and attributes.
    """
    banned = {
        "requests",
        "httpx",
        "urllib",
        "socket",
        "videodb",
        "anthropic",
        "openai",
        "session",
        "connection",
        "engine",
        "execute",
        "open",
    }
    tree = ast.parse(inspect.getsource(module))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            used.add(node.attr.lower())
    found = sorted(used & banned)
    assert not found, f"{module.__name__} references {found}; it must stay pure"


def test_measurement_modules_import_nothing_that_reaches_a_network():
    for module in (intervals, evaluators):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                assert "videodb" not in name and "videodb" not in names


# -- evidence provenance ---------------------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        "retrieval_run_id",
        "media_asset_id",
        "origin",
        "role",
        "modality",
        "start_seconds",
        "confidence_band",
        "provider_snapshot",
    ],
)
def test_evidence_provenance_columns_are_mandatory(column):
    """PRD s11 provenance fields must be non-nullable, not best-effort."""
    assert EvidenceItem.__table__.columns[column].nullable is False


@pytest.mark.parametrize("column", ["end_seconds", "provider_score"])
def test_optional_provider_fields_stay_nullable(column):
    """The provider may omit these; null must stay distinct from zero."""
    assert EvidenceItem.__table__.columns[column].nullable is True


# -- honest language -------------------------------------------------------


def test_ui_never_uses_forbidden_certainty_language():
    """UX_SPEC.md s6 lists language the interface must avoid."""
    web = SRC / "web"
    banned = [
        "definitely absent",
        "fully compliant",
        "ai approved",
        "guaranteed violation",
        "100% compliant",
    ]
    for path in list(web.glob("*.js")) + list(web.glob("*.html")):
        text = path.read_text().lower()
        for phrase in banned:
            assert phrase not in text, f"{path.name} contains {phrase!r}"


# -- provider input constraints (learned from live verification) -----------


def test_visual_sampling_interval_is_a_positive_integer():
    """VideoDB rejects a non-integer `time` in the scene extraction_config.

    Verified against the live API 2026-07-26. The default was 2.0 (a float),
    which would have failed EVERY visual index in production.
    """
    from adproof.retrieval.plan import VISUAL_SECONDS_PER_SCENE

    assert isinstance(VISUAL_SECONDS_PER_SCENE, int)
    assert VISUAL_SECONDS_PER_SCENE >= 1


@pytest.mark.parametrize("bad", [2.5, 0, -1, 0.5])
def test_adapter_rejects_invalid_sampling_interval_before_calling_provider(bad):
    from adproof.providers.errors import ProviderRejected

    adapter = videodb_adapter.VideoDBAdapter.__new__(
        videodb_adapter.VideoDBAdapter
    )
    with pytest.raises(ProviderRejected, match="positive integer"):
        videodb_adapter.VideoDBAdapter.index_scenes(
            adapter,
            "m-test",
            prompt="p",
            index_name="i",
            seconds_per_scene=bad,
        )
