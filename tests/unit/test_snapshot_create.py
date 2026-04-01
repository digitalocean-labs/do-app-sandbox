"""Tests for snapshot_id parameter in Sandbox.create()."""

from unittest.mock import MagicMock, patch

import pytest

from do_app_sandbox.exceptions import SpacesNotConfiguredError
from do_app_sandbox.types import SpacesConfig


class TestSnapshotCreateParam:
    """Tests for Sandbox.create(snapshot_id=...) parameter."""

    @patch("do_app_sandbox.sandbox._ensure_doctl_available")
    @patch("do_app_sandbox.sandbox.Deployer")
    def test_snapshot_id_generates_presigned_url(self, mock_deployer_cls, mock_doctl):
        """snapshot_id generates presigned URL and passes it as env var."""
        from do_app_sandbox.sandbox import Sandbox

        mock_deployer = MagicMock()
        mock_deployer_cls.return_value = mock_deployer
        mock_deployer.create_app.return_value = (
            MagicMock(app_id="app-123", url="https://test.app"),
            None,
        )
        mock_deployer.wait_ready.return_value = MagicMock(app_id="app-123", url="https://test.app")

        mock_spaces = MagicMock()
        mock_spaces.generate_presigned_download_url.return_value = "https://spaces.example.com/signed-url"

        spaces_config = SpacesConfig(
            bucket="test-bucket",
            region="nyc3",
            access_key="key",
            secret_key="secret",
        )

        with patch("do_app_sandbox.spaces.SpacesClient", return_value=mock_spaces):
            sandbox = Sandbox.create(
                image="python",
                snapshot_id="snap-abc123",
                spaces_config=spaces_config,
            )

        # Verify presigned URL was generated
        mock_spaces.generate_presigned_download_url.assert_called_once()
        call_args = mock_spaces.generate_presigned_download_url.call_args
        assert "snap-abc123/archive.tar.gz" in call_args[0][0]

        # Verify env var was passed to create_app
        create_call = mock_deployer.create_app.call_args
        env_vars = create_call.kwargs.get("env_vars") or create_call[1].get("env_vars")
        assert env_vars is not None
        assert "SANDBOX_SNAPSHOT_URL" in env_vars
        assert env_vars["SANDBOX_SNAPSHOT_URL"] == "https://spaces.example.com/signed-url"

    @patch("do_app_sandbox.sandbox._ensure_doctl_available")
    @patch("do_app_sandbox.sandbox.Deployer")
    def test_no_snapshot_id_no_env_vars(self, mock_deployer_cls, mock_doctl):
        """Without snapshot_id, no snapshot env vars are passed."""
        from do_app_sandbox.sandbox import Sandbox

        mock_deployer = MagicMock()
        mock_deployer_cls.return_value = mock_deployer
        mock_deployer.create_app.return_value = (
            MagicMock(app_id="app-123", url="https://test.app"),
            None,
        )
        mock_deployer.wait_ready.return_value = MagicMock(app_id="app-123", url="https://test.app")

        sandbox = Sandbox.create(image="python")

        # Verify no env vars passed
        create_call = mock_deployer.create_app.call_args
        env_vars = create_call.kwargs.get("env_vars") or create_call[1].get("env_vars")
        assert env_vars is None

    def test_snapshot_id_without_spaces_raises(self):
        """snapshot_id without spaces config raises SpacesNotConfiguredError."""
        from do_app_sandbox.sandbox import Sandbox

        with patch("do_app_sandbox.sandbox._ensure_doctl_available"):
            with patch("do_app_sandbox.spaces.create_spaces_config_from_env", return_value=None):
                with pytest.raises(SpacesNotConfiguredError):
                    Sandbox.create(
                        image="python",
                        snapshot_id="snap-abc123",
                    )


class TestDeployerEnvVarInjection:
    """Tests for env var injection into app spec."""

    def test_env_vars_added_to_worker_spec(self):
        """Env vars are injected into worker spec."""
        from do_app_sandbox.deployer import Deployer

        deployer = Deployer(registry="test-owner")
        spec, _ = deployer._generate_app_spec(
            name="test-sandbox",
            image="python",
            component_type="worker",
            env_vars={"SANDBOX_SNAPSHOT_URL": "https://example.com/snapshot"},
        )

        assert "SANDBOX_SNAPSHOT_URL" in spec
        assert "https://example.com/snapshot" in spec
        assert "envs:" in spec

    def test_env_vars_added_to_service_spec(self):
        """Env vars are injected into service spec."""
        from do_app_sandbox.deployer import Deployer

        deployer = Deployer(registry="test-owner")
        spec, _ = deployer._generate_app_spec(
            name="test-sandbox",
            image="python",
            component_type="service",
            env_vars={"SANDBOX_SNAPSHOT_URL": "https://example.com/snapshot"},
        )

        assert "SANDBOX_SNAPSHOT_URL" in spec
        assert "https://example.com/snapshot" in spec

    def test_no_env_vars_spec_unchanged(self):
        """Without env_vars, spec is unchanged."""
        from do_app_sandbox.deployer import Deployer

        deployer = Deployer(registry="test-owner")
        spec_with, _ = deployer._generate_app_spec(
            name="test",
            image="python",
            component_type="worker",
        )
        spec_without, _ = deployer._generate_app_spec(
            name="test",
            image="python",
            component_type="worker",
            env_vars=None,
        )

        assert spec_with == spec_without
        assert "SANDBOX_SNAPSHOT_URL" not in spec_with
