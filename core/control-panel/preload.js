// Preload bridge: exposes a tiny IPC API to the control-panel web page so the
// injected (frameless) title bar can drive the Electron window.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ikarosPanel', {
  minimize: () => ipcRenderer.send('panel:minimize'),
  close: () => ipcRenderer.send('panel:close'),
  toggleAlwaysOnTop: () => ipcRenderer.send('panel:toggle-top'),
  isAlwaysOnTop: () => ipcRenderer.invoke('panel:is-top'),
  // main -> page: notify when always-on-top state changes (e.g. via tray menu)
  onTopChange: (cb) => ipcRenderer.on('panel:top-changed', (_e, on) => cb(on)),
});
