load("@bzlws//:index.bzl", "bzlws_copy")
load("@bzlws//:index.bzl", "bzlws_link")

cc_binary(
    name = "mkevent",
    srcs = ["mkevent.cc"],
)

cc_binary(
    name = "mkeventd_open514",
    srcs = ["mkeventd_open514.cc"],
)

bzlws_copy(
    name = "deploy_mkevent",
    out = "{FILENAME}",
    force = True,
    srcs = [
        ":mkevent",
        ":mkeventd_open514",
    ],
)
