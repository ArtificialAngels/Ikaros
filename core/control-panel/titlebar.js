// Injected into the control-panel page to render a frameless title bar and
// wire its buttons to window.ikarosPanel (provided by preload.js).
(function () {
  if (document.getElementById('ikaros-titlebar')) return;
  var bar = document.createElement('div');
  bar.id = 'ikaros-titlebar';
  bar.innerHTML =
    '<span class="title">Ikaros 控制面板</span>' +
    '<button class="tb-btn" id="tb-min" title="最小化">—</button>' +
    '<button class="tb-btn" id="tb-top" title="置顶显示">\u{1F4CC}</button>' +
    '<button class="tb-btn close" id="tb-close" title="关闭（最小化到托盘）">✕</button>';
  (document.body || document.documentElement).appendChild(bar);

  function api() { return window.ikarosPanel || {}; }

  document.getElementById('tb-min').addEventListener('click', function () {
    if (api().minimize) api().minimize();
  });
  document.getElementById('tb-close').addEventListener('click', function () {
    if (api().close) api().close();
  });

  var topBtn = document.getElementById('tb-top');
  topBtn.addEventListener('click', function () {
    if (api().toggleAlwaysOnTop) api().toggleAlwaysOnTop();
  });
  if (api().isAlwaysOnTop) {
    Promise.resolve(api().isAlwaysOnTop()).then(function (on) {
      if (on) topBtn.classList.add('active');
    });
  }
  if (api().onTopChange) {
    api().onTopChange(function (on) { topBtn.classList.toggle('active', !!on); });
  }
})();
