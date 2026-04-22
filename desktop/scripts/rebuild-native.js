#!/usr/bin/env node
// Rebuild native dependencies (node-pty) for the correct Electron architecture.
//
// Problem: on Apple Silicon Macs running a Rosetta (x86_64) shell, npm and
// electron-builder both see process.arch === "x64" and compile native modules
// for x86_64.  But the Electron binary itself is arm64 (the electron npm
// package uses OS-level detection).  This creates an architecture mismatch
// that makes node-pty's pty.spawn() fail with "posix_spawnp failed".
//
// Fix: detect Apple Silicon via sysctl and pass --arch arm64 explicitly.

const { execSync } = require("child_process");

function getTargetArch() {
  if (process.platform !== "darwin") return process.arch;
  try {
    const val = execSync("sysctl -n hw.optional.arm64", { encoding: "utf-8" }).trim();
    if (val === "1") return "arm64";
  } catch {}
  return process.arch;
}

const arch = getTargetArch();
console.log(`[rebuild-native] platform=${process.platform} process.arch=${process.arch} target=${arch}`);

try {
  execSync(`npx electron-builder install-app-deps --arch ${arch}`, {
    stdio: "inherit",
    cwd: __dirname + "/..",
  });
} catch (err) {
  console.error("[rebuild-native] electron-builder install-app-deps failed:", err.message);
  process.exit(1);
}

// Verify the resulting binary matches the target
try {
  const ptyNode = require("path").join(__dirname, "..", "node_modules", "node-pty", "build", "Release", "pty.node");
  const info = execSync(`file ${JSON.stringify(ptyNode)}`, { encoding: "utf-8" }).trim();
  console.log(`[rebuild-native] ${info}`);
  if (arch === "arm64" && !info.includes("arm64")) {
    console.warn("[rebuild-native] WARNING: pty.node is NOT arm64 — Electron terminal will fail!");
  }
} catch {}
