"""A deliberately tiny Kubernetes client: it can list and delete the pods of one of this project's
four known services, and nothing else.

Scope is the safety property here. There is no generic `apply`, no exec-into-pod, no arbitrary
patch; `restart_service()` refuses any service name outside `dataset_tools.config.SERVICES`, so
even a caller that somehow got past the Safety Validator cannot use this class to touch Postgres,
Prometheus, or anything else in the namespace.

"Restart" is `delete pod` on purpose: every service is managed by a Deployment (see k8s/*.yaml), so
deleting the pod makes Kubernetes recreate it - the same self-healing behavior Week 3 already
observed live when ledger-service crash-looped until Postgres was ready. That is idiomatic, and it
avoids needing a mutable-patch permission just to force a rollout.

The `kubernetes` package is imported lazily inside methods rather than at module import time, so
that importing `ai_orchestration` (for tests, for the Streamlit console, on a machine with no
cluster) never requires it to be installed.
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import config as ai_config

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config  # noqa: E402

log = logging.getLogger("ai_orchestration.execution.kubernetes_client")


class KubernetesUnavailableError(RuntimeError):
    """Raised when the cluster cannot be reached or the client library is not installed. Always
    surfaced as an execution failure, never as a silent success."""


class KubernetesClient:
    def __init__(self, namespace: Optional[str] = None):
        self.namespace = namespace or ai_config.KUBERNETES_NAMESPACE
        self._core_v1 = None

    def _api(self):
        if self._core_v1 is not None:
            return self._core_v1
        try:
            from kubernetes import client, config as k8s_config  # noqa: PLC0415 - see module docstring
        except ImportError as exc:  # pragma: no cover - exercised only where the lib is absent
            raise KubernetesUnavailableError(
                "The 'kubernetes' package is not installed - cannot execute cluster actions."
            ) from exc

        try:
            k8s_config.load_kube_config()
        except Exception:  # noqa: BLE001 - fall back to in-cluster config, then report honestly
            try:
                k8s_config.load_incluster_config()
            except Exception as exc:  # noqa: BLE001
                raise KubernetesUnavailableError(
                    f"No usable Kubernetes configuration found (tried kubeconfig, then in-cluster): {exc}"
                ) from exc

        self._core_v1 = client.CoreV1Api()
        return self._core_v1

    def list_service_pods(self, service: str) -> list[str]:
        self._require_known_service(service)
        api = self._api()
        try:
            pods = api.list_namespaced_pod(namespace=self.namespace, label_selector=f"app={service}")
        except Exception as exc:  # noqa: BLE001 - any client/transport error is "unavailable"
            raise KubernetesUnavailableError(f"Failed to list pods for '{service}': {exc}") from exc
        return [p.metadata.name for p in pods.items]

    def restart_service(self, service: str) -> dict:
        """Deletes every pod labelled `app=<service>` in the namespace so the Deployment recreates
        them. Returns an audit record of exactly which pods were deleted - never a bare bool, since
        Week 12 needs to be able to say what was actually done."""
        self._require_known_service(service)
        api = self._api()
        pod_names = self.list_service_pods(service)
        if not pod_names:
            raise KubernetesUnavailableError(
                f"No pods found with label app={service} in namespace '{self.namespace}' - "
                f"refusing to report a restart that did not happen."
            )

        deleted: list[str] = []
        for name in pod_names:
            try:
                api.delete_namespaced_pod(name=name, namespace=self.namespace)
            except Exception as exc:  # noqa: BLE001
                raise KubernetesUnavailableError(
                    f"Failed to delete pod '{name}' for service '{service}': {exc}"
                ) from exc
            deleted.append(name)
            log.info("Deleted pod '%s' (service '%s') - Deployment will recreate it", name, service)

        return {"namespace": self.namespace, "service": service, "deleted_pods": deleted}

    @staticmethod
    def _require_known_service(service: str) -> None:
        if service not in dt_config.SERVICES:
            raise KubernetesUnavailableError(
                f"'{service}' is not one of this project's services {dt_config.SERVICES} - "
                f"this client will not act on it."
            )
