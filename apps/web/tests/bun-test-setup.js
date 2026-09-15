import { JSDOM } from "jsdom";
import { afterEach, vi } from "vitest";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  pretendToBeVisual: true,
  url: "https://drive.uln.me",
});

const storage = new Map();
const localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
  key: (index) => [...storage.keys()][index] ?? null,
  get length() {
    return storage.size;
  },
};

Object.defineProperty(dom.window, "localStorage", {
  configurable: true,
  value: localStorage,
});
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: localStorage,
});
Object.defineProperty(globalThis, "FormData", {
  configurable: true,
  value: dom.window.FormData,
});

for (const property of Object.getOwnPropertyNames(dom.window)) {
  if (property in globalThis) continue;
  const descriptor = Object.getOwnPropertyDescriptor(dom.window, property);
  if (descriptor) Object.defineProperty(globalThis, property, descriptor);
}

Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", {
  configurable: true,
  value: true,
  writable: true,
});

if (typeof vi.hoisted !== "function") {
  Object.defineProperty(vi, "hoisted", {
    configurable: true,
    value: (factory) => factory(),
  });
}

if (typeof vi.mocked !== "function") {
  Object.defineProperty(vi, "mocked", {
    configurable: true,
    value: (value) => value,
  });
}

const originalGlobals = new Map();

if (typeof vi.stubGlobal !== "function") {
  Object.defineProperty(vi, "stubGlobal", {
    configurable: true,
    value: (name, value) => {
      if (!originalGlobals.has(name)) {
        originalGlobals.set(
          name,
          Object.getOwnPropertyDescriptor(globalThis, name),
        );
      }
      Object.defineProperty(globalThis, name, {
        configurable: true,
        value,
        writable: true,
      });
      return vi;
    },
  });
}

if (typeof vi.unstubAllGlobals !== "function") {
  Object.defineProperty(vi, "unstubAllGlobals", {
    configurable: true,
    value: () => {
      for (const [name, descriptor] of originalGlobals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else Reflect.deleteProperty(globalThis, name);
      }
      originalGlobals.clear();
      return vi;
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});
