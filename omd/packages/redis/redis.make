REDIS := redis
REDIS_VERS := 6.2.6
REDIS_DIR := $(REDIS)-$(REDIS_VERS)

REDIS_BUILD := $(BUILD_HELPER_DIR)/$(REDIS_DIR)-build
REDIS_INSTALL := $(BUILD_HELPER_DIR)/$(REDIS_DIR)-install

$(REDIS_BUILD):
	bazel build @redis//:build
	$(TOUCH) $@

$(REDIS_INSTALL): $(REDIS_BUILD)
	bazel run @redis//:deploy
	$(RSYNC) --chmod=Du=rwx,Dg=rx,Do=rx,Fu=rw,Fg=r,Fo=r build/by_bazel/redis/ $(DESTDIR)$(OMD_ROOT)/
	$(TOUCH) $@


# Is the following line needed? What does it do? Do we need the intermediate install?
#	$(MKDIR) $(REDIS_INSTALL_DIR)/skel/var/redis
