import pytest
from pydantic import ValidationError

from alicia.canon import Watch


def test_watch_rejects_channels_the_runtime_cannot_deliver():
    with pytest.raises(ValidationError, match="Slack channels only"):
        Watch(target="work", watcher="owner", trigger_condition="review", notify_channel="email:owner@example.com")
