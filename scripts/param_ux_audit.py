"""Run majordom's zigbee visibility/role mapping across the ENTIRE ZCL surface (all zigpy
clusters), replicating controller.py's parameter-build rules, and bucket the result the way the
iOS app would show it: main / user / setting / system. Read-only static audit — no hardware."""
import importlib, inspect
from zigpy.zcl import Cluster
from zigpy.zcl.foundation import ZCLAttributeAccess
from majordom_zigbee.zigbee_spec import MAIN_PARAMETER_BY_CLUSTER, SYSTEM_CLUSTERS

MODULES = ["general","measurement","lighting","hvac","closures","homeautomation",
           "security","smartenergy","protocol","lightlink","manufacturer_specific"]

def clusters():
    seen = {}
    for m in MODULES:
        try: mod = importlib.import_module(f"zigpy.zcl.clusters.{m}")
        except Exception: continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Cluster) and obj is not Cluster and getattr(obj, "cluster_id", None) is not None:
                seen.setdefault(obj.cluster_id, obj)
    return dict(sorted(seen.items()))

def attr_visibility(cluster_id, attr_id, access):
    # replicate controller.py:302-312
    vis = "system"
    if attr_id < 0xF000 or cluster_id not in SYSTEM_CLUSTERS:
        if access & ZCLAttributeAccess.Report: vis = "user"
        elif access & ZCLAttributeAccess.Write: vis = "setting"
    return vis

def attr_role(access):
    r = bool(access & ZCLAttributeAccess.Read); w = bool(access & ZCLAttributeAccess.Write)
    return "control" if (r and w) else "sensor" if r else "event"

buckets_total = {"user":0,"setting":0,"system":0}
main_clusters = set(MAIN_PARAMETER_BY_CLUSTER)
rows = []
for cid, cls in clusters().items():
    is_sys = cid in SYSTEM_CLUSTERS
    b = {"user":[], "setting":[], "system":[]}
    attrs = getattr(cls, "attributes", {}) or {}
    for aid, adef in attrs.items():
        if not isinstance(aid, int): continue
        access = getattr(adef, "access", None)
        if access is None: continue
        vis = attr_visibility(cid, aid, access)
        b[vis].append(getattr(adef, "name", str(aid)))
    # client-side commands -> user (or system if system cluster)
    cmds = getattr(cls, "client_commands", {}) or {}
    cmd_vis = "system" if is_sys else "user"
    cmd_names = [getattr(c, "name", str(k)) for k, c in cmds.items() if isinstance(k, int)]
    b[cmd_vis].extend(f"{n}()" for n in cmd_names)
    for k in buckets_total: buckets_total[k] += len(b[k])
    rows.append((cid, cls.__name__, is_sys, cid in main_clusters, b))

print(f"=== ZIGBEE full-ZCL mapping: {len(rows)} clusters ===")
print(f"total params by bucket: user={buckets_total['user']} setting={buckets_total['setting']} system={buckets_total['system']}")
print(f"clusters providing a MAIN parameter: {sorted(hex(c) for c in main_clusters)}\n")

# spotlight: common device-facing clusters + anomalies
SPOT = {0x0006:"OnOff",0x0008:"LevelControl",0x0300:"Color",0x0201:"Thermostat",0x0202:"FanControl",
        0x0102:"WindowCovering",0x0101:"DoorLock",0x0402:"TempMeas",0x0405:"Humidity",0x0400:"Illuminance",
        0x0000:"Basic(SYS)",0x0001:"PowerConfig",0x0b04:"ElectricalMeas",0x0500:"IAS_Zone"}
for cid, name, is_sys, has_main, b in rows:
    if cid in SPOT:
        print(f"--- 0x{cid:04X} {name}{' [SYSTEM]' if is_sys else ''}{' [MAIN]' if has_main else ''} ---")
        for k in ("user","setting","system"):
            if b[k]: print(f"   {k:7}({len(b[k])}): {', '.join(b[k][:12])}{' …' if len(b[k])>12 else ''}")

# anomaly scan: standard attributes on SYSTEM clusters that leaked to user/setting (the `or` bug)
print("\n=== ANOMALY: user/setting params on SYSTEM clusters (should arguably be system) ===")
for cid, name, is_sys, has_main, b in rows:
    if is_sys and (b["user"] or b["setting"]):
        print(f"  0x{cid:04X} {name}: user={b['user'][:6]} setting={b['setting'][:6]}")
