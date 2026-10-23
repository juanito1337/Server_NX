"""Short deterministic test directory labels for Windows path-bound packages.

The full node ID stays in JUnit/plan.json. Long test names must not consume the
application's legitimate component-path budget before the test even begins.
"""
import hashlib
import pytest


@pytest.fixture
def tmp_path(request,tmp_path_factory):
    label=hashlib.sha256(request.node.nodeid.encode('utf-8')).hexdigest()[:12]
    return tmp_path_factory.mktemp(label)
