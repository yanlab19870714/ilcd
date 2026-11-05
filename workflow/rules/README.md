One `<module>.smk` per module. A rule's `shell:` is a single `ilcd <module> <command> ...` call; inputs and outputs
are produced by Python helpers that call `ilcd.common.paths`, so the layout is defined in one place.
