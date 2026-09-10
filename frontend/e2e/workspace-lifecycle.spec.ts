import AxeBuilder from "@axe-core/playwright";
import { expect,test } from "@playwright/test";
import {mockWorkspace,previewIdentityFixture,responseBarrier,loginAsB} from "./fixtures/workspace-lifecycle";

test("141 当前任务可明确冻结为自动计划，网页不静默刷新", async ({ page }) => {
  await mockWorkspace(page);
  const fixture=previewIdentityFixture("本人",2);
  await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
  await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
  let sent:any=null;
  await page.route("**/api/tasks/from-workspace",route=>{sent={body:route.request().postDataJSON(),key:route.request().headers()["idempotency-key"]};return route.fulfill({status:201,json:{task_id:"schedule-one",workspace:{source_task_id:"identity-task",source_revision:2}}});});
  await page.goto("/data-prep?task=identity-task&revision=2");
  await page.getByRole("button",{name:"设为自动任务",exact:true}).click();
  const dialog=page.getByRole("dialog");
  await expect(dialog.getByText("网页沿用已确认快照，不会自动重新采集。",{exact:true})).toBeVisible();
  await dialog.getByLabel("计划名称",{exact:true}).fill("我的证据计划");
  await dialog.getByRole("button",{name:"确认创建计划",exact:true}).click();
  await expect.poll(()=>sent).not.toBeNull();
  expect(sent.body).toMatchObject({task_id:"identity-task",revision:2,name:"我的证据计划"});
  expect(sent.key).toBeTruthy();
  await expect(dialog.getByRole("link",{name:"查看自动任务",exact:true})).toHaveAttribute("href","/tasks");
});

test("141 自动计划立即执行未知后刷新只查原次，正式结果回同一任务版本", async ({ page }) => {
  await mockWorkspace(page);
  const plan={task_id:"schedule-one",name:"冻结资料计划",source:"workspace",status:"active",user_input:"比较证据",trigger_type:"cron",cron_expr:"0 9 * * *",run_count:0,workspace:{source_task_id:"identity-task",source_revision:2,timezone:"Asia/Shanghai"}};
  await page.route("**/api/tasks",route=>route.fulfill({json:[plan]}));
  await page.route("**/api/tasks/templates",route=>route.fulfill({json:[]}));
  let posts=0,key="",found=false;
  const occurrence={occurrence_id:"occ-exact",state:"completed",workspace_task_id:"consumer-exact",workspace_revision:1,runtime_run_id:"run-exact",output_ids:["output-exact"]};
  await page.route("**/api/tasks/schedule-one/run_now",route=>{posts++;key=route.request().headers()["idempotency-key"];return route.fulfill({status:503,json:{detail:"响应尚未确认"}});});
  await page.route("**/api/tasks/schedule-one/occurrences*",route=>{const query=new URL(route.request().url()).searchParams; if(query.has("idempotency_key")){expect(query.get("idempotency_key")).toBe(key);found=true;return route.fulfill({json:{items:[occurrence]}});}return route.fulfill({json:{items:[]}});});
  await page.goto("/tasks");
  await page.getByRole("button",{name:"立即执行一次",exact:true}).click();
  await expect(page.getByText(/结果尚未确认，请查询原请求/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button",{name:"立即执行一次",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"查询原次执行",exact:true}).click();
  await expect.poll(()=>found).toBe(true);
  await expect(page.getByRole("link",{name:"查看本次正式结果",exact:true})).toHaveAttribute("href","/data-prep?task=consumer-exact&revision=1");
  expect(posts).toBe(1);
});

test("141 反馈读取当前版本后提交，未知刷新只查询原反馈且可继续修改", async ({page})=>{
  await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);
  await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
  await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
  let writes:any[]=[],saved:any={version:3,comment:"已保存说明",request_key:"old",output_id:"本人-V2-output"};
  await page.route("**/api/semantic-workspace/tasks/identity-task/feedback*",route=>{
    if(route.request().method()==="GET")return route.fulfill({json:{feedback:saved}});
    const body=route.request().postDataJSON();writes.push(body);saved={...body,version:4,request_key:route.request().headers()["idempotency-key"]};return route.fulfill({status:503,json:{detail:"仅响应未知"}});
  });
  await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"反馈正式结果",exact:true}).click();
  await expect(page.getByText("当前已保存反馈 V3",{exact:true})).toBeVisible();
  await page.getByLabel("补充说明",{exact:true}).fill("新的明确反馈");await page.getByRole("button",{name:"提交反馈",exact:true}).click();
  await expect(page.getByText(/结果尚未确认，请查询原请求/)).toBeVisible();
  expect(writes[0]).toMatchObject({revision:2,output_id:"本人-V2-output",expected_version:3,comment:"新的明确反馈"});
  await page.reload();await page.getByRole("button",{name:"反馈正式结果",exact:true}).click();await page.getByRole("button",{name:"查询原请求",exact:true}).click();
  await expect(page.getByText("已确认保存。",{exact:true})).toBeVisible();expect(writes).toHaveLength(1);
  await page.getByRole("button",{name:"修改反馈",exact:true}).click();await expect(page.getByRole("button",{name:"提交反馈",exact:true})).toBeEnabled();
});

for(const [width,theme] of [[390,"light"],[390,"dark"],[1440,"light"],[1440,"dark"]] as const){
 test(`141 ${width} ${theme} 计划键盘和输入法保稿`,async({page},testInfo)=>{
  await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);
  await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
  await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
  await page.setViewportSize({width,height:900});await page.goto("/data-prep?task=identity-task&revision=2");
  await page.evaluate(theme=>document.documentElement.classList.toggle("dark",theme==="dark"),theme);
  if(width===390)await page.getByRole("button",{name:"关闭结果预览",exact:true}).click();
  const opener=page.getByRole("button",{name:"设为自动任务",exact:true});await opener.click();
  const dialog=page.getByRole("dialog"),name=dialog.getByLabel("计划名称",{exact:true});await expect(name).toBeFocused();await name.fill("保留输入法草稿");
  await name.dispatchEvent("keydown",{key:"Escape",isComposing:true});await expect(dialog).toBeVisible();
  await name.press("Shift+Tab");await expect(dialog.getByRole("button",{name:"确认创建计划",exact:true})).toBeFocused();
  await name.focus();await name.press("Escape");await expect(dialog).toHaveCount(0);await expect(opener).toBeFocused();
  await opener.click();await expect(name).toHaveValue("保留输入法草稿");
  expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
  const audit=await new AxeBuilder({page}).include('[role="dialog"]').analyze();expect(audit.violations).toEqual([]);
  await page.screenshot({path:testInfo.outputPath(`plan-${width}-${theme}.png`)});
 });
}

test("141 创建计划未知刷新仅查原键，确定拒绝才允许修正",async({page})=>{
 await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);
 await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
 let posts=0,key="",queries=0;
 await page.route("**/api/tasks/from-workspace",route=>{posts++;key=route.request().headers()["idempotency-key"];return route.fulfill({status:503,json:{detail:"结果未知"}});});
 await page.route("**/api/tasks/workspace-plans/by-key?*",route=>{queries++;expect(new URL(route.request().url()).searchParams.get("idempotency_key")).toBe(key);return route.fulfill({status:404,json:{detail:"尚未登记"}});});
 await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"设为自动任务",exact:true}).click();await page.getByRole("button",{name:"确认创建计划",exact:true}).dblclick();
 await expect(page.getByText(/结果尚未确认，请查询原请求/)).toBeVisible();expect(posts).toBe(1);
 await page.reload();await page.getByRole("button",{name:"设为自动任务",exact:true}).click();await expect(page.getByLabel("计划名称",{exact:true})).toBeDisabled();
 await page.getByRole("button",{name:"查询原请求",exact:true}).click();await expect.poll(()=>queries).toBe(1);expect(posts).toBe(1);
 await page.getByRole("button",{name:"返回任务",exact:true}).click();await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("141 同源另一页新草稿先写入，旧成功不得覆盖",async({page})=>{
 await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);const entered=responseBarrier(),release=responseBarrier();
 await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
 await page.route("**/api/tasks/from-workspace",async route=>{entered.release();await release.promise;return route.fulfill({status:201,json:{task_id:"old-schedule"}});});
 await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"设为自动任务",exact:true}).click();await page.getByRole("button",{name:"确认创建计划",exact:true}).click();await entered.promise;
 // 同窗口写存储不触发storage事件，覆盖跨页事件尚未送达的CAS窗口。
 const storageKey=await page.evaluate(()=>Object.keys(localStorage).find(key=>key.includes(".plan.identity-task.2"))!);
 const changed=await page.evaluate(key=>{const value=JSON.parse(localStorage.getItem(key)!);value.draft.name="另一页面的新资料计划";value.pending={key:"new-pending",payload:{task_id:"new-task"}};const raw=JSON.stringify(value);localStorage.setItem(key,raw);return raw;},storageKey);
 release.release();await expect(page.getByText("此草稿已在另一页面更新，请重新打开后查看。",{exact:true})).toBeVisible();
 expect(await page.evaluate(key=>localStorage.getItem(key),storageKey)).toBe(changed);await expect(page.getByRole("link",{name:"查看自动任务",exact:true})).toHaveCount(0);
});

test("141 云模型重复使用需明确确认，确定拒绝可改稿使用新键",async({page})=>{
 await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);fixture.detail.external_api_confirmed=true;
 await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
 const writes:any[]=[];
 await page.route("**/api/tasks/from-workspace",route=>{writes.push({key:route.request().headers()["idempotency-key"],body:route.request().postDataJSON()});return writes.length===1?route.fulfill({status:409,headers:{"X-Mangrove-Lifecycle-Outcome":"rejected"},json:{detail:"原连接已失效"}}):route.fulfill({status:201,json:{task_id:"accepted-plan"}});});
 await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"设为自动任务",exact:true}).click();
 const submit=page.getByRole("button",{name:"确认创建计划",exact:true});await expect(submit).toBeDisabled();await page.getByRole("checkbox",{name:/按计划重复使用原模型连接/}).check();await submit.click();
 await expect(page.getByText(/未保存，可修改后重新确认/)).toBeVisible();await expect(page.getByLabel("计划名称",{exact:true})).toBeEnabled();await page.getByLabel("计划名称",{exact:true}).fill("明确再次确认");await submit.click();
 await expect.poll(()=>writes.length).toBe(2);expect(writes[0].key).not.toBe(writes[1].key);expect(writes[1].body).toMatchObject({task_id:"identity-task",revision:2,repeat_external_confirmed:true});
});

test("141 管理员反馈只读来源元数据，明确审计后标记版本回答摘要",async({page})=>{
 await mockWorkspace(page);let audits=0;
 const item={id:-9,source_kind:"workspace",task_id:"source-task",revision:3,output_id:"formal-output",output_sha256:"a".repeat(64),rating:"up",user_id:"u1",display_name:"本人",username:"tester",reasons:[],created_at:"2026-09-09",status:"pending",has_comment:true,has_admin_note:false,content_available:true};
 await page.route("**/api/feedback/overview",route=>route.fulfill({json:{total_sessions:1,total_up:1,total_down:0,total_pending:1,down_rate:0,reason_counts:{},daily:[]}}));
 await page.route("**/api/feedback/list?*",route=>route.fulfill({json:{items:[item],total:1}}));
 await page.route("**/api/feedback/-9/audit-content",route=>{audits++;expect(route.request().postDataJSON().reason).toBe("核对任务版本来源");return route.fulfill({json:{event_id:"audit-exact",answer_kind:"revision_summary",source:{source_kind:"workspace",task_id:"source-task",revision:3,output_id:"formal-output",output_sha256:"a".repeat(64)},content:{question:"原任务问题",answer:"原版本摘要",comment:"审计后才可见描述",admin_note:null},truncated:false,content_bytes:100}});});
 await page.goto("/feedback");await expect(page.getByText("工作台 · source-task · V3 · 正式结果 formal-output",{exact:true})).toBeVisible();await expect(page.getByText("审计后才可见描述",{exact:true})).toHaveCount(0);expect(audits).toBe(0);
 await page.getByRole("button",{name:"审计查看业务内容",exact:true}).click();await page.getByLabel("查看原因",{exact:true}).fill("核对任务版本来源");await page.getByRole("button",{name:"提交审计并查看",exact:true}).click();
 await expect(page.getByText("此任务版本的回答摘要（非完整文件）",{exact:true})).toBeVisible();await expect(page.getByText("审计后才可见描述",{exact:true})).toBeVisible();
 await page.getByRole("dialog").getByRole("button",{name:"关闭",exact:true}).click();await expect(page.getByText("审计后才可见描述",{exact:true})).toHaveCount(0);
});

test("141 Owner切换丢弃旧计划响应，不继承旧待确认草稿",async({page})=>{
 await mockWorkspace(page);const entered=responseBarrier(),release=responseBarrier();let owner="A";
 await page.route("**/api/auth/login",route=>{owner="B";return route.fulfill({json:{user_id:"owner-b",username:"owner-b",display_name:"乙",role:"admin"}});});
 await page.route("**/api/auth/logout",route=>route.fulfill({json:{ok:true}}));
 await page.route("**/api/semantic-workspace/tasks?*",route=>route.fulfill({json:[previewIdentityFixture(owner,2).task]}));
 await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:previewIdentityFixture(owner,2).detail}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
 await page.route("**/api/tasks/from-workspace",async route=>{entered.release();await release.promise;return route.fulfill({status:201,json:{task_id:"owner-a-plan"}});});
 await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"设为自动任务",exact:true}).click();await page.getByLabel("计划名称",{exact:true}).fill("A的私有计划草稿");await page.getByRole("button",{name:"确认创建计划",exact:true}).click();await entered.promise;
 await page.getByRole("button",{name:"返回任务",exact:true}).click();await page.getByRole("button",{name:"打开导航",exact:true}).click();await page.getByTitle("退出登录").click();await loginAsB(page);
 release.release();await page.getByRole("button",{name:"打开导航",exact:true}).click();await page.getByRole("link",{name:"任务工作台",exact:true}).click();await page.getByRole("button",{name:/B的结果任务/}).click();await page.getByRole("button",{name:"设为自动任务",exact:true}).click();
 await expect(page.getByLabel("计划名称",{exact:true})).toHaveValue("B的结果任务");await expect(page.getByRole("button",{name:"查询原请求",exact:true})).toHaveCount(0);await expect(page.getByText("A的私有计划草稿",{exact:true})).toHaveCount(0);
});

test("141 查询逐次执行接受原次最新终态，不被旧运行投影覆盖",async({page})=>{
 await mockWorkspace(page);let posts=0;
 await page.route("**/api/tasks",route=>route.fulfill({json:[{task_id:"s-refresh",name:"需要确认的计划",source:"workspace",status:"active",user_input:"固定证据",trigger_type:"cron",cron_expr:"0 9 * * *",run_count:0}]}));
 await page.route("**/api/tasks/templates",route=>route.fulfill({json:[]}));
 const original={occurrence_id:"occ-refresh",state:"awaiting_action",workspace_task_id:"task-original",workspace_revision:1,runtime_run_id:"run-original",output_ids:[]};
 await page.route("**/api/tasks/s-refresh/run_now",route=>{posts++;return route.fulfill({json:{occurrence:original}});});
 await page.route("**/api/tasks/s-refresh/occurrences",route=>route.fulfill({json:{items:[{...original,state:"completed",output_ids:["formal-original"]}]}}));
 await page.goto("/tasks");await page.getByRole("button",{name:"立即执行一次",exact:true}).click();await expect(page.getByText("原任务需要补充或确认",{exact:true})).toBeVisible();
 await page.getByRole("button",{name:"查看逐次执行",exact:true}).click();await expect(page.getByRole("link",{name:"查看本次正式结果",exact:true})).toHaveAttribute("href","/data-prep?task=task-original&revision=1");await expect(page.getByText("原任务需要补充或确认",{exact:true})).toHaveCount(0);expect(posts).toBe(1);
});

test("141 反馈未知已被另一设备更新，可明确放弃本地等待再读当前版本",async({page})=>{
 await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);let posts=0,current:any=null;
 await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/feedback*",route=>{if(route.request().method()==="GET")return route.fulfill({json:{feedback:current}});posts++;current={version:2,request_key:"other-device",comment:"另一设备已保存"};return route.fulfill({status:503,json:{detail:"原响应未知"}});});
 await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"反馈正式结果",exact:true}).click();await page.getByRole("button",{name:"提交反馈",exact:true}).click();await expect(page.getByText(/结果尚未确认，请查询原请求/)).toBeVisible();
 await page.getByRole("button",{name:"查询原请求",exact:true}).click();await expect(page.getByText(/尚未确认原反馈版本/)).toBeVisible();
 await page.getByRole("button",{name:"放弃本地待确认",exact:true}).click();await expect(page.getByText(/原反馈可能已保存，此操作不会撤销服务端反馈/)).toBeVisible();await page.getByRole("button",{name:"保留原请求继续等待",exact:true}).click();await expect(page.getByRole("button",{name:"提交反馈",exact:true})).toHaveCount(0);
 await page.getByRole("button",{name:"放弃本地待确认",exact:true}).click();await page.getByRole("button",{name:"明确放弃本地等待",exact:true}).click();await expect(page.getByText("当前已保存反馈 V2",{exact:true})).toBeVisible();await expect(page.getByRole("button",{name:"提交反馈",exact:true})).toBeEnabled();expect(posts).toBe(1);
});

test("141 非法计划名称和时区留在可编辑草稿，零POST",async({page})=>{
 await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);let posts=0;
 await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
 await page.route("**/api/tasks/from-workspace",route=>{posts++;return route.fulfill({status:422,json:{detail:[]}});});
 await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"设为自动任务",exact:true}).click();
 const name=page.getByLabel("计划名称",{exact:true}),submit=page.getByRole("button",{name:"确认创建计划",exact:true});
 await name.evaluate(node=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(node,"长".repeat(161));node.dispatchEvent(new Event("input",{bubbles:true}));});await expect(submit).toBeDisabled();await name.fill("可修正名称");await page.getByLabel("所在时区",{exact:true}).fill("");await expect(submit).toBeDisabled();await page.getByLabel("所在时区",{exact:true}).fill("invalid/time-zone");await expect(submit).toBeDisabled();await page.getByLabel("所在时区",{exact:true}).fill("Asia/Shanghai");await expect(submit).toBeEnabled();expect(posts).toBe(0);await expect(name).toBeEnabled();
});

test("141 原反馈被新版覆盖后仍按原键确认收据，不重发或伪装旧正文",async({page})=>{
 await mockWorkspace(page);const fixture=previewIdentityFixture("本人",2);let posts=0,receipt:any=null,current:any=null;
 await page.route("**/api/semantic-workspace/tasks/identity-task*",route=>route.fulfill({json:fixture.detail}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/turns",route=>route.fulfill({json:{turns:[],results:[],proposals:[]}}));
 await page.route("**/api/semantic-workspace/tasks/identity-task/feedback*",route=>{
  if(route.request().method()==="GET"){const key=new URL(route.request().url()).searchParams.get("idempotency_key");return route.fulfill({json:{feedback:current,receipt:key===receipt?.request_key?receipt:null}});}
  posts++;receipt={request_key:route.request().headers()["idempotency-key"],version:1,task_id:"identity-task",revision:2,output_id:"本人-V2-output",result:"saved",receipt_only:true};current={version:2,request_key:"other-device",comment:"后来的当前反馈"};return route.fulfill({status:503,json:{detail:"响应未知"}});
 });
 await page.goto("/data-prep?task=identity-task&revision=2");await page.getByRole("button",{name:"反馈正式结果",exact:true}).click();await page.getByLabel("补充说明",{exact:true}).fill("原请求说明");await page.getByRole("button",{name:"提交反馈",exact:true}).click();await expect(page.getByText(/结果尚未确认，请查询原请求/)).toBeVisible();
 await page.reload();await page.getByRole("button",{name:"反馈正式结果",exact:true}).click();await page.getByRole("button",{name:"查询原请求",exact:true}).click();await expect(page.getByText("已确认保存。",{exact:true})).toBeVisible();await expect(page.getByText("当前已保存反馈 V2",{exact:true})).toBeVisible();await expect(page.getByText(/原请求已保存为反馈 V1/)).toBeVisible();expect(posts).toBe(1);
 await page.getByRole("button",{name:"修改反馈",exact:true}).click();await expect(page.getByRole("button",{name:"提交反馈",exact:true})).toBeEnabled();
});

test("141 两个执行查询共用在途门，原次终态不会被旧列表覆盖",async({page})=>{
 await mockWorkspace(page);const entered=responseBarrier(),release=responseBarrier();let queries=0;
 await page.route("**/api/tasks",route=>route.fulfill({json:[{task_id:"s-order",name:"顺序计划",source:"workspace",status:"active",user_input:"证据",trigger_type:"cron",cron_expr:"0 9 * * *",run_count:0}]}));await page.route("**/api/tasks/templates",route=>route.fulfill({json:[]}));
 const row={occurrence_id:"occ-order",state:"running",workspace_task_id:"task-order",workspace_revision:1,runtime_run_id:"run-order",output_ids:[]};
 await page.route("**/api/tasks/s-order/run_now",route=>route.fulfill({status:503,json:{detail:"未知"}}));
 await page.route("**/api/tasks/s-order/occurrences*",async route=>{if(new URL(route.request().url()).searchParams.has("idempotency_key")){queries++;return route.fulfill({json:{items:[{...row,state:"completed",output_ids:["formal-order"]}]}});}entered.release();await release.promise;return route.fulfill({json:{items:[row]}});});
 await page.goto("/tasks");await page.getByRole("button",{name:"立即执行一次",exact:true}).click();await expect(page.getByText(/结果尚未确认，请查询原请求/)).toBeVisible();await page.getByRole("button",{name:"查看逐次执行",exact:true}).click();await entered.promise;
 await expect(page.getByRole("button",{name:"查询原次执行",exact:true})).toBeDisabled();expect(queries).toBe(0);release.release();await expect(page.getByRole("button",{name:"查询原次执行",exact:true})).toBeEnabled();await page.getByRole("button",{name:"查询原次执行",exact:true}).click();await expect(page.getByRole("link",{name:"查看本次正式结果",exact:true})).toHaveAttribute("href","/data-prep?task=task-order&revision=1");
});
