"""一次性：把主界面底部那排灯渲染出来看效果。用完即删。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from canoe_client.session import session  # noqa: E402
from canoe_client.ui.main_view import MainView  # noqa: E402

app = QApplication([])

LINKS = "\n".join(
    f"ss://2022-blake3-aes-128-gcm:AAAA:BBBB@node{i}.example.com:33222#节点{i}"
    for i in range(1, 7)
)

view = MainView()
view.show()
session.login("YunJuDian", "")

for n in (0, 1, 2, 5):
    view._apply_subscription("\n".join(LINKS.splitlines()[:n]))
    view._active = 0
    view._sync_active()
    app.processEvents()
    name = f"_lights{n}.png"
    view.grab().save(name)
    print(f"{n} 个节点 -> 亮 {view.lights.count()} 盏，当前第 {view.lights.active() + 1} 盏，"
          f"节点名「{view.node_label.text()}」  {name}")

# 有 2 个节点时，点第二盏应当切过去
view._apply_subscription("\n".join(LINKS.splitlines()[:2]))
view._active = 0
view._sync_active()
app.processEvents()
view.grab().save("_lights2_first.png")
view.lights.node_selected.emit(1)
view._switch_node(1)
app.processEvents()
view.grab().save("_lights2_second.png")
print(f"点第二盏 -> 当前第 {view.lights.active() + 1} 盏，节点名「{view.node_label.text()}」")

# 超出 6 个不理
view._apply_subscription(LINKS + "\n" + LINKS.replace("node", "extra.node"))
print("喂 12 条链接 -> 灯只有", view.lights.count(), "盏")
