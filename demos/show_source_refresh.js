(() => {
  document.body.style.background =
    'repeating-conic-gradient(#df91c7 0% 25%, #f8e4f1 0% 50%) 0 / 48px 48px';
  document.getElementById('root').style.position = 'relative';
  document.getElementById('source-refresh-demo')?.remove();
  const panel = document.createElement('div');
  panel.id = 'source-refresh-demo';
  panel.style.cssText = `position:fixed;z-index:10000;left:22px;bottom:48px;
    padding:14px;background:#18242e;color:white;border-radius:8px;
    font:14px system-ui;box-shadow:0 2px 15px #0005`;
  panel.innerHTML = `
    <div style="font-weight:600;margin-bottom:8px">Whole-source refresh · synthetic data</div>
    <div style="margin-bottom:10px">
      Checkerboard: below viewer · black/rings: acquired image · this panel: above
    </div>
    <button id="demo-add">Add position</button>
    <button id="demo-rewrite">Rewrite last</button>
    <button id="demo-hint">Repeat unchanged hint</button>
    <div id="demo-state" role="status" style="margin-top:8px">Ready</div>`;
  document.body.append(panel);
  const state = panel.querySelector('#demo-state');
  const act = async action => {
    panel.querySelectorAll('button').forEach(button => { button.disabled = true; });
    try { state.textContent = await action(); }
    catch (error) { state.textContent = String(error); }
    finally {
      panel.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
  };
  panel.querySelector('#demo-add').onclick = () => act(() => pywebview.api.publish(false));
  panel.querySelector('#demo-rewrite').onclick = () => act(() => pywebview.api.publish(true));
  panel.querySelector('#demo-hint').onclick = () => act(async () => {
    const result = await fetch('/api/announce', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({wrote_image_in_place: true}),
    });
    if (!result.ok) throw new Error('Notification failed: ' + result.status);
    return 'Unchanged hint sent — image should stay unchanged';
  });
  return true;
})()
