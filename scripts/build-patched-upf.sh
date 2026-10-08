#!/usr/bin/env bash
# Build only; never modifies a running deployment. Archives must be pre-fetched.
set -euo pipefail
work_dir=${1:?Usage: build-patched-upf.sh ABSOLUTE_BUILD_DIR}
case "$work_dir" in /*) ;; *) echo 'Build directory must be absolute' >&2; exit 1 ;; esac
cd "$work_dir"
printf '%s\n' \
  '9e9b755d63b36acf30c12a9a3fc379243714c1c6d3dd72861da637f336ebb35b  go1.25.5.linux-amd64.tar.gz' \
  '00813361fa91c2e02592e6b508bde73b3d796fb6a2a69add4ce9d1bc36e3d17a  source.tar.gz' \
  | sha256sum -c -
test -x go/bin/go || tar -xzf go1.25.5.linux-amd64.tar.gz
test -d free5gc-go-upf-04c1ab6 || tar -xzf source.tar.gz
export PATH="$work_dir/go/bin:$PATH"
export GOTOOLCHAIN=local
export GOMAXPROCS=2
cd free5gc-go-upf-04c1ab6
git apply --check ../go-upf-v1.2.10-remotesess-nil.patch
sha256sum go.mod go.sum > ../dependencies-before.sha256

# Run the upstream regression on the original implementation first.
git apply --include=internal/pfcp/node_test.go ../go-upf-v1.2.10-remotesess-nil.patch
if go test -mod=readonly -p 2 ./internal/pfcp -run '^TestLocalNode$/remote_sess_skips_deleted_local_slots$' \
     -count=1 -v > ../regression-before.txt 2>&1; then
  echo 'Expected the original implementation to fail; refusing to continue' >&2
  exit 1
fi
grep -q 'panic: runtime error: invalid memory address or nil pointer dereference' ../regression-before.txt
git apply --include=internal/pfcp/node.go ../go-upf-v1.2.10-remotesess-nil.patch
git apply --reverse --check ../go-upf-v1.2.10-remotesess-nil.patch
go test -mod=readonly -p 2 ./internal/pfcp -run '^TestLocalNode$' -count=1 -v \
  > ../regression-after.txt 2>&1
go vet -mod=readonly ./internal/pfcp > ../vet.txt 2>&1
sha256sum -c ../dependencies-before.sha256

build_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)
version_pkg=github.com/free5gc/util/version
CGO_ENABLED=0 go build -mod=readonly -p 2 -trimpath \
  -ldflags "-s -w -X ${version_pkg}.VERSION=v4.2.2-nilfix -X ${version_pkg}.BUILD_TIME=${build_time} -X ${version_pkg}.COMMIT_HASH=04c1ab64+cbad64a4-nilfix -X ${version_pkg}.COMMIT_TIME=2026-04-18T13:02:58Z" \
  -o ../upf ./cmd
go version -m ../upf > ../binary-build-info.txt
cd "$work_dir"
sha256sum source.tar.gz go1.25.5.linux-amd64.tar.gz go-upf-v1.2.10-remotesess-nil.patch upf \
  > build-sha256.txt
echo 'Regression reproduced on original code; patched TestLocalNode and vet passed; binary built.'
