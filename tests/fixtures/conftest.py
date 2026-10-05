# The folders under tests/fixtures are sample repositories that Causix analyzes,
# not part of our test suite. Stop pytest from collecting their test files.
collect_ignore_glob = ["*/*", "*/*/*", "*/*/*/*"]
