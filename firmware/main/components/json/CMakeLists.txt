# Compatibility shim for the pre-IDF-6.0 "json" component name.
#
# espressif/esp-sr (2.2.x) still declares `REQUIRES json`, but IDF 6.0
# removed the built-in json wrapper component. The real cJSON implementation
# comes from the managed espressif/cjson component; this empty local shim
# only provides the old component name so dependency resolution succeeds
# and propagates cjson's include paths to whoever requires "json".
idf_component_register(REQUIRES cjson)
