ARG IMAGE_ALMALINUX_9
# hadolint ignore=DL3006
FROM ${IMAGE_ALMALINUX_9} as base

SHELL ["/bin/bash", "-c"]
ENV LC_ALL=C.UTF-8 LANG=C.UTF-8 PATH="/opt/bin:${PATH}"

RUN yum -y --enablerepo=crb  makecache && yum repolist --enablerepo=crb \
    && yum -y --allowerasing --enablerepo=crb install \
    bison \
    curl \
    enchant \
    gcc \
    gcc-c++ \
    git \
    krb5-devel \
    make \
    openldap-devel \
    openldap-compat \
    postfix \
    graphviz-gd \
    strace \
    sudo \
    vim \
    which \
    && yum clean all

RUN yum -y makecache \
    && yum -y --enablerepo=crb install \
    bind-utils \
    curl \
    curl-devel \
    expat-devel \
    flex \
    flex-devel \
    freeradius-devel \
    gcc \
    gcc-c++ \
    gd-devel \
    gettext \
    gtk-doc \
    httpd-devel \
    kernel-headers \
    libXpm-devel \
    libevent-devel \
    libffi-devel \
    libgsf-devel \
    libiscsi-devel \
    libjpeg-devel \
    libmpc-devel \
    libpcap-devel \
    libstdc++-devel \
    libstdc++-static \
    libtool \
    libtool-ltdl \
    libtool-ltdl-devel \
    libuuid-devel \
    libxml2-devel \
    libxcrypt-compat \
    xmlsec1-devel \
    mariadb-devel \
    mpfr-devel \
    ncurses-devel \
    openssh-clients \
    openssl-devel \
    pango-devel \
    patch \
    pcre-devel \
    perl \
    perl-ExtUtils-Embed \
    perl-IO-Zlib \
    perl-Locale-Maketext-Simple \
    perl-Time-HiRes \
    perl-devel \
    php \
    postgresql-devel \
    procps \
    readline-devel \
    rpcbind \
    rpm-build \
    rrdtool-devel \
    rsync \
    samba-client \
    sqlite-devel \
    texinfo \
    tk-devel \
    wget \
    which \
    xmlsec1-devel \
    xmlsec1-openssl-devel \
    && yum clean all
# --nogpgcheck: Workaround for failing installation, not locally reproducable
RUN yum -y makecache \
    && yum -y --enablerepo=crb reinstall \
    kernel-headers \
    --nogpgcheck \
    && yum clean all

# epel release is needed for joe
# --nogpgcheck: Workaround for failing installation, not locally reproducable
RUN yum -y makecache \
    && yum -y install \
    epel-release \
    --nogpgcheck \
    && yum clean all

# New packages that are not needed for the build toolchain above should be added here.
# We avoid rebuild of the whole previous steps this way
# --nogpgcheck: Workaround for failing installation, not locally reproducable
RUN yum -y makecache \
    && yum -y --enablerepo=crb install \
    joe \
    iputils \
    vim \
    --nogpgcheck \
    && yum clean all

# Avoid the annobin chaos on CentOS 8, part 2:
# See also: omd/omd.spec.in
RUN if test -f /usr/lib/rpm/redhat/redhat-annobin-cc1; then \
        rm -f /usr/lib/rpm/redhat/redhat-annobin-cc1 \
        && touch /usr/lib/rpm/redhat/redhat-annobin-cc1; \
    fi

# Install our standard tool chain for building
# - gnu-toolchain is needed for compiling all the C++ stuff
# - cmake is needed for e.g. building re2
# - openssl is needed by Python 3.7+
# - python is needed by our build / test chain
FROM base as builder
ARG NEXUS_ARCHIVES_URL
ARG NEXUS_USERNAME
ARG NEXUS_PASSWORD
ARG DISTRO
ARG BRANCH_VERSION
ENV NEXUS_ARCHIVES_URL="$NEXUS_ARCHIVES_URL" NEXUS_USERNAME="$NEXUS_USERNAME" NEXUS_PASSWORD="$NEXUS_PASSWORD" DISTRO="$DISTRO" BRANCH_VERSION="$BRANCH_VERSION"

COPY scripts/* /opt/
RUN /opt/install-gnu-toolchain.sh
RUN /opt/install-valgrind.sh
RUN /opt/install-cmake.sh
RUN /opt/install-protobuf-cpp.sh
RUN /opt/install-openssl.sh
RUN /opt/install-python.sh
RUN /opt/install-freetds.sh

# Now shrink all the binaries and libraries we produced to build a small image
# in the next step
RUN /opt/strip_binaries /opt

# Run this AFTER strip_binaries!!
RUN /opt/install-bazel.sh

### Actual Image ###
FROM base

# Copy our standard tool chain for building
COPY --from=builder /opt /opt
ARG DISTRO
ARG BRANCH_VERSION
ENV DISTRO="$DISTRO" BRANCH_VERSION="$BRANCH_VERSION"

# Set symlinks
RUN /opt/install-gnu-toolchain.sh link-only
RUN /opt/install-valgrind.sh link-only
RUN /opt/install-cmake.sh link-only
RUN /opt/install-protobuf-cpp.sh --link-only
RUN /opt/install-python.sh link-only
RUN /opt/install-freetds.sh link-only
RUN /opt/install-bazel.sh link-only

# Install non cached dependencies
RUN /opt/install-pipenv.sh
RUN /opt/install-cmk-dependencies.sh
RUN /opt/install-patchelf.sh

# The /etc/fstab does not exist in the base image we use. A missing fstab prevents OMD from
# using a tmpfs for /omd/sites/[site]/tmp, which we want to have during our tests. We can
# simply solve this by pre-creating the empty file here.
RUN touch /etc/fstab

# Ensure all our build containers have the jenkins user (with same uid/gid). The non privileged
# jobs will be executed as this user in the container
RUN groupadd -g 1000 jenkins \
    && useradd -m -u 1001 -g 1000 -s /bin/bash jenkins

RUN mkdir -p /home/jenkins/.cache/ \
    && chown jenkins:jenkins /home/jenkins/.cache/

ARG VERS_TAG
RUN echo $VERS_TAG > /version.txt

LABEL \
    com.tribe29.image_type="build-image"
