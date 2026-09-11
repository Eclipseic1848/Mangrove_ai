import "../../../src/main";
import { sessionCommand } from "../../../src/lib/api";
// 仅合成会话走真实状态接缝，由产品main负责Owner重挂载。
Object.assign(window, { navigationFixture: { changeOwner: () => sessionCommand("/api/auth/login", { username: "synthetic-other", password: "synthetic-only" }) } });
