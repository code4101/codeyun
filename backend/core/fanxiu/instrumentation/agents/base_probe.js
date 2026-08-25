function moduleRecord(module) {
  return {
    name: module.name,
    path: module.path,
    base: module.base.toString(),
    size: module.size,
  };
}

function healthRecord() {
  return {
    process: {
      id: Process.id,
      arch: Process.arch,
      platform: Process.platform,
      pointerSize: Process.pointerSize,
      pageSize: Process.pageSize,
      codeSigningPolicy: Process.codeSigningPolicy,
    },
    javaAvailable: typeof Java !== "undefined" && Java.available,
  };
}

rpc.exports = {
  health() {
    return healthRecord();
  },

  snapshot(moduleNames) {
    const requested = new Set((moduleNames || []).map((value) => String(value).toLowerCase()));
    const modules = Process.enumerateModules()
      .filter((module) => requested.size === 0 || requested.has(module.name.toLowerCase()))
      .map(moduleRecord);
    return {
      ...healthRecord(),
      modules,
    };
  },
};
