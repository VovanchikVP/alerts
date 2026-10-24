#!/usr/bin/env bash
# Выпускает kubeconfig для CI с доступом только к namespace alerts.
# Запускать на ноде k3s (или с админским kubeconfig в KUBECONFIG):
#
#   ./make-kubeconfig.sh https://k3s.example.com:6443 | tee kubeconfig.b64
#
# Результат — в секрет KUBE_CONFIG нужного репозитория:
#   gh secret set KUBE_CONFIG --repo <owner>/<repo> < kubeconfig.b64
set -euo pipefail

NAMESPACE=alerts
SECRET=alerts-deployer-token
HERE="$(cd "$(dirname "$0")" && pwd)"

SERVER="${1:-$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')}"
case "$SERVER" in
  *127.0.0.1*|*localhost*)
    echo "Адрес сервера $SERVER недоступен с раннеров GitHub." >&2
    echo "Передайте внешний адрес: $0 https://<host>:6443" >&2
    exit 1
    ;;
esac

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >&2
kubectl apply -f "$HERE/deployer-rbac.yaml" >&2

# Токен выпускается контроллером асинхронно
for _ in $(seq 1 15); do
  TOKEN="$(kubectl -n "$NAMESPACE" get secret "$SECRET" -o jsonpath='{.data.token}' 2>/dev/null || true)"
  [ -n "$TOKEN" ] && break
  sleep 1
done
[ -n "${TOKEN:-}" ] || { echo "токен так и не выпущен: kubectl -n $NAMESPACE describe secret $SECRET" >&2; exit 1; }

CA="$(kubectl -n "$NAMESPACE" get secret "$SECRET" -o jsonpath='{.data.ca\.crt}')"

b64() { base64 -w0 2>/dev/null || base64 | tr -d '\n'; }

cat <<KUBECONFIG | b64
apiVersion: v1
kind: Config
current-context: alerts
clusters:
  - name: k3s
    cluster:
      server: ${SERVER}
      certificate-authority-data: ${CA}
contexts:
  - name: alerts
    context:
      cluster: k3s
      namespace: ${NAMESPACE}
      user: alerts-deployer
users:
  - name: alerts-deployer
    user:
      token: $(echo "$TOKEN" | base64 -d)
KUBECONFIG
