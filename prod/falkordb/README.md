Steps for installing kubeblocks and adding falcon db add-on

```
kubectl create -f https://github.com/apecloud/kubeblocks/releases/download/v1.0.1/kubeblocks_crds.yaml

helm repo add kubeblocks https://apecloud.github.io/helm-charts
helm repo update

helm install kubeblocks kubeblocks/kubeblocks --namespace kb-system --create-namespace

```

KBCLI installation

curl -fsSL https://kubeblocks.io/installer/install_cli.sh | bash

```
kbcli version

kbcli addon install falkordb --version 1.0.1
```
