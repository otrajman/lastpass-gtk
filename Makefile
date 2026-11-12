# Packaging runs inside an Ubuntu 24.04 container (packaging/Dockerfile) so the
# binaries work on 24.04 and every newer distro. Outputs land in dist/.
IMAGE  ?= lastpass-gtk-build
DOCKER ?= docker
RUN     = $(DOCKER) run --rm --user $$(id -u):$$(id -g) -e HOME=/tmp -v "$(CURDIR)":/src -w /src $(IMAGE)

.PHONY: all dist deb appimage lpass test image clean

all: lpass

# dev build of lpass + the mock-server lpass-test, on this machine
lpass:
	LPASS_TEST=1 scripts/build-lpass.sh build/lpass

# unit tests + integration tests against lastpass-cli's mock server (in the build container)
test: image
	$(RUN) sh -c 'LPASS_TEST=1 scripts/build-lpass.sh build/lpass-ci >/dev/null && \
	  python3 -m unittest discover -v -s tests -t .'

image:
	$(DOCKER) build -t $(IMAGE) packaging

dist: deb appimage

deb: image
	$(RUN) packaging/deb/build-deb.sh

appimage: image
	$(RUN) packaging/appimage/build-appimage.sh

clean:
	rm -rf build dist
