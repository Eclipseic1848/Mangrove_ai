import { createRoot } from "react-dom/client";
import { Chat } from "../../../src/pages/Chat";
import { Markdown } from "../../../src/components/Markdown";
import "../../../src/index.css";
createRoot(document.getElementById("root")!).render(new URLSearchParams(location.search).get("mode") === "chat" ? <Chat /> : <main>
  <section aria-label="原安全资源模式"><Markdown safeResources>{"![同源示例](/api/data-sources/uploads/image/content)\n\n[原普通链接](/local-link)"}</Markdown></section>
  <section aria-label="仅外站资源模式"><Markdown externalImagesAsLinks>{"![协议相对外站图](//image.example.invalid/relative.png)\n\n![HTTP外站图](http://image.example.invalid/http.png)"}</Markdown></section>
</main>);
