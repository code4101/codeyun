const childProcess = require("child_process");

function withWindowsHide(options) {
  if (options == null) {
    return { windowsHide: true };
  }
  if (typeof options === "function") {
    return options;
  }
  if (typeof options === "object" && !Array.isArray(options)) {
    if (options.windowsHide === undefined) {
      return { ...options, windowsHide: true };
    }
  }
  return options;
}

function patchSpawn(name) {
  const original = childProcess[name];
  childProcess[name] = function patchedSpawn(command, args, options) {
    if (Array.isArray(args)) {
      return original.call(this, command, args, withWindowsHide(options));
    }
    return original.call(this, command, withWindowsHide(args));
  };
}

function patchExec(name) {
  const original = childProcess[name];
  childProcess[name] = function patchedExec(command, options, callback) {
    if (typeof options === "function") {
      return original.call(this, command, withWindowsHide(undefined), options);
    }
    return original.call(this, command, withWindowsHide(options), callback);
  };
}

function patchExecFile(name) {
  const original = childProcess[name];
  childProcess[name] = function patchedExecFile(file, args, options, callback) {
    if (Array.isArray(args)) {
      if (typeof options === "function") {
        return original.call(this, file, args, withWindowsHide(undefined), options);
      }
      return original.call(this, file, args, withWindowsHide(options), callback);
    }
    if (typeof args === "function") {
      return original.call(this, file, withWindowsHide(undefined), args);
    }
    return original.call(this, file, withWindowsHide(args), options);
  };
}

patchSpawn("spawn");
patchSpawn("spawnSync");
patchExec("exec");
patchExec("execSync");
patchExecFile("execFile");
patchExecFile("execFileSync");
patchSpawn("fork");
