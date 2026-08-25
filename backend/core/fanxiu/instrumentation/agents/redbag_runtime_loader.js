function nativeBridgeModule() {
  return Process.getModuleByName("libnativebridge.so");
}

function openBridge(remotePath) {
  const module = nativeBridgeModule();
  const getNamespace = new NativeFunction(
    module.getExportByName("NativeBridgeGetExportedNamespace"),
    "pointer",
    ["pointer"]
  );
  const loadLibrary = new NativeFunction(
    module.getExportByName("NativeBridgeLoadLibraryExt"),
    "pointer",
    ["pointer", "int", "pointer"]
  );
  const getTrampoline = new NativeFunction(
    module.getExportByName("NativeBridgeGetTrampoline"),
    "pointer",
    ["pointer", "pointer", "pointer", "uint"]
  );
  const parent = getNamespace(
    Memory.allocUtf8String("classloader-namespace")
  );
  if (parent.isNull()) {
    throw new Error("无法取得游戏 classloader NativeBridge 命名空间");
  }
  const handle = loadLibrary(
    Memory.allocUtf8String(remotePath),
    2,
    parent
  );
  if (handle.isNull()) {
    throw new Error("无法加载红包 ARM64 适配器");
  }
  return { handle, getTrampoline };
}

function trampoline(bridge, name, shorty, returnType, argumentTypes) {
  const shortyPointer = Memory.allocUtf8String(shorty);
  const address = bridge.getTrampoline(
    bridge.handle,
    Memory.allocUtf8String(name),
    shortyPointer,
    shorty.length
  );
  if (address.isNull()) {
    throw new Error(`无法解析红包 ARM64 适配器符号：${name}`);
  }
  if (address.toString().toLowerCase().startsWith("0xdead")) {
    throw new Error(
      `红包 ARM64 适配器返回失败哨兵：${name}=${address}`
    );
  }
  const range = Process.findRangeByAddress(address);
  if (range === null || !range.protection.includes("x")) {
    throw new Error(
      `红包 ARM64 适配器符号不可执行：${name}=${address}`
    );
  }
  return {
    address,
    call: new NativeFunction(address, returnType, argumentTypes),
  };
}

function unityMainThread() {
  const threads = Process.enumerateThreads()
    .filter(
      thread =>
        thread.name === "UnityMain" && thread.state === "waiting"
    )
    .sort((left, right) => left.id - right.id);
  if (threads.length === 0) {
    throw new Error("没有处于 waiting 的 UnityMain 线程");
  }
  return threads[0];
}

async function probeBridge(bridge, targetThread) {
  const probe = trampoline(
    bridge,
    "codeyun_redbag_bridge_probe",
    "IJ",
    "int",
    ["pointer", "pointer", "int64"]
  );
  const result = await Process.runOnThread(
    targetThread.id,
    () => probe.call(
      ptr(0),
      ptr(0),
      int64("0x434f444558")
    )
  );
  if (result !== 1) {
    throw new Error(`红包 ARM64 适配器 ABI 校验失败：${result}`);
  }
  return probe.address.toString();
}

rpc.exports = {
  async probe(remotePath) {
    const bridge = openBridge(remotePath);
    const targetThread = unityMainThread();
    return {
      ok: true,
      probeTrampoline: await probeBridge(bridge, targetThread),
      thread: {
        id: targetThread.id,
        name: targetThread.name,
        state: targetThread.state,
      },
    };
  },

  async ensure(remotePath, addresses) {
    const bridge = openBridge(remotePath);
    const targetThread = unityMainThread();
    const probeTrampoline = await probeBridge(bridge, targetThread);
    const ensure = trampoline(
      bridge,
      "codeyun_ensure_redbag_manager",
      "IJJJJJ",
      "int",
      [
        "pointer",
        "pointer",
        "int64",
        "int64",
        "int64",
        "int64",
        "int64",
      ]
    );
    const status = await Process.runOnThread(
      targetThread.id,
      () => ensure.call(
        ptr(0),
        ptr(0),
        int64(addresses.state),
        int64(addresses.gettop),
        int64(addresses.loadstring),
        int64(addresses.pcall),
        int64(addresses.settop)
      )
    );
    return {
      ok: status === 0,
      status,
      thread: {
        id: targetThread.id,
        name: targetThread.name,
        state: targetThread.state,
      },
      probeTrampoline,
      ensureTrampoline: ensure.address.toString(),
    };
  },
};
