import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "laowu-sidebar.html").read_text(encoding="utf-8")


class SidebarBehaviorTests(unittest.TestCase):
    def node(self, script):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for sidebar behavior tests")
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def function(self, start, end):
        return SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]

    def test_native_only_route_exposes_auto(self):
        function = self.function("  function routeCanAuto(", "  async function readTaskResult(") + self.function("    function updateLaunchGroups(", "    function updateLaunchRouting()")
        result = self.node('const routes=[{id:"a",enabled:true,nativeCodexFallback:true,groups:[]}];const routeSelect={value:"a"},groupSelect={};let lastGroupId="",lastRouteId="";const esc=x=>String(x);' + function + 'updateLaunchGroups();process.stdout.write(JSON.stringify(groupSelect));')
        self.assertFalse(result["disabled"])
        self.assertIn('value="auto"', result["innerHTML"])

    def test_free_editor_displays_the_actual_role(self):
        function = self.function("  function profileEditorMarkup()", "  async function loadProfiles()")
        result = self.node('const profileEditingId="saved";const profileSnapshot={profiles:[{id:"saved",name:"Free",role:"free",instructions:"fixture"}]};const esc=x=>String(x),profileRoleLabel=x=>x;'+function+'process.stdout.write(JSON.stringify(profileEditorMarkup()));')
        self.assertIn('value="free" selected', result)

    def test_auto_summary_does_not_use_ordinal_for_unnamed_group(self):
        function = self.function("  function routeModeLabel(", "  async function loadCodexPermission()")
        result = self.node('const route={autoMode:"sequential",nativeCodexFallback:true,groups:[{name:"",displayName:"分组 7",auto:true,enabled:true}]};' + function + 'process.stdout.write(JSON.stringify(routeModeLabel(route)));')
        self.assertIn("未命名分组", result)
        self.assertNotIn("分组 7", result)

    def test_profiles_view_has_one_to_eight_global_concurrency_selector(self):
        function = self.function("  function profileConcurrencyMarkup()", "  function profileEditorMarkup()")
        result = self.node('const profileSnapshot={subagentConcurrency:3};const esc=x=>String(x);' + function + 'process.stdout.write(JSON.stringify(profileConcurrencyMarkup()));')
        self.assertIn('id="subagent-concurrency"', result)
        self.assertIn('value="1"', result)
        self.assertIn('value="8"', result)
        self.assertIn('value="3" selected', result)
        self.assertIn('action:"concurrency",concurrency:value', SOURCE)
        self.assertIn('新任务最多同时运行 ${value} 个代理', SOURCE)

    def test_result_reader_fetches_every_page_in_order(self):
        function = self.function("  async function readTaskResult(", "  function profileEditorMarkup()") if "  async function readTaskResult(" in SOURCE else "async function readTaskResult(id,page){return page}"
        result = self.node('const calls=[];const textOf=x=>x.structuredContent;const rpc=async(m,p)=>{calls.push(p.arguments.offset);return {structuredContent:{activity_id:"a",status:"completed",ready:true,result:"tail",offset:12000,next_offset:null,total_chars:12004,complete:true}}};'+function+'readTaskResult("a",{activity_id:"a",status:"completed",ready:true,result:"x".repeat(12000),offset:0,next_offset:12000,total_chars:12004,complete:false}).then(x=>process.stdout.write(JSON.stringify({result:x.result,calls})));')
        self.assertEqual(result["calls"], [12000])
        self.assertTrue(result["result"].endswith("tail"))
        self.assertEqual(len(result["result"]), 12004)

    def test_queued_task_has_stop_button_and_disabled_delete(self):
        fragment = self.function("    const stopButton=", "    const masterRecallHtml=")
        result = self.node('const activity={id:"a",status:"queued",retained:false,hasFullResult:false};const deleteConfirmId=null;'+fragment+'process.stdout.write(JSON.stringify({stopButton,recordActionButtons}));')
        self.assertIn('id="stop-task"', result["stopButton"])
        self.assertIn('data-activity-action="delete" disabled', result["recordActionButtons"])

    def test_complete_sidebar_script_parses(self):
        script = SOURCE[SOURCE.index("<script>") + len("<script>"):SOURCE.index("</script>")]
        result = subprocess.run([shutil.which("node"), "--check", "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_language_helpers_default_persist_and_translate(self):
        self.assertIn("const LANGUAGE_KEY =", SOURCE)
        helpers = self.function("  const LANGUAGE_KEY =", "  function applyLanguage(")
        result = self.node(
            'const storage={value:null,getItem(){return this.value},setItem(_key,value){this.value=value}};'
            + helpers
            + 'const first=loadLanguage(storage);const saved=saveLanguage(storage,"en");'
            + 'const route=translateUiText("仅启用“默认”，请选择 Auto 或具体分组。","en");'
            + 'const duration=translateUiText("2 分 5 秒","en");const unknown=translateUiText("My custom group","en");'
            + 'const concurrency=translateUiText("已保存；新任务最多同时运行 3 个代理。","en");'
            + 'process.stdout.write(JSON.stringify({first,saved,stored:storage.value,en:translateUiText("设置","en"),zh:translateUiText("Settings","zh-CN"),route,duration,concurrency,unknown}));'
        )
        self.assertEqual(result, {"first": "zh-CN", "saved": "en", "stored": "en", "en": "Settings", "zh": "设置", "route": "Only “默认” is enabled. Choose Auto or a specific group.", "duration": "2m 5s", "concurrency": "Saved. New tasks can run up to 3 agents at once.", "unknown": "My custom group"})
        self.assertIn('id="language-select"', SOURCE)
        self.assertIn('"laowu-language"', SOURCE)
        self.assertIn('document.documentElement.lang!==language', SOURCE)
        self.assertIn('script,style,textarea,.bubble,.task-short,.title,.profile-name,.user-copy,[data-user-copy]', SOURCE)
        self.assertIn('"laowu-font-size",LANGUAGE_KEY', SOURCE)

    def test_language_switch_localizes_ui_but_preserves_user_content(self):
        helpers = self.function("  const LANGUAGE_KEY =", "  function applyLanguage(")
        apply = self.function("  function applyLanguage(", "  let languageObserver=")
        result = self.node(
            'const localStorage={getItem(){return null},setItem(){}};const document={documentElement:{lang:"zh-CN"},title:"乌合之众"};'
            + helpers
            + apply
            + 'const text=value=>({nodeType:3,nodeValue:value,parentElement:null});'
            + 'const element=(classes=[],attrs={},children=[],tagName="div")=>{const node={nodeType:1,classes,attrs:new Map(Object.entries(attrs)),childNodes:children,parentElement:null,tagName,matches(selector){return selector.split(",").some(item=>{item=item.trim();return item[0]==="."?this.classes.includes(item.slice(1)):item==="[data-user-copy]"?this.attrs.has("data-user-copy"):this.tagName===item})},closest(selector){for(let item=this;item;item=item.parentElement)if(item.matches(selector))return item;return null},hasAttribute(name){return this.attrs.has(name)},getAttribute(name){return this.attrs.get(name)},setAttribute(name,value){this.attrs.set(name,value)}};for(const child of children)child.parentElement=node;return node};'
            + 'const label=text("设置"),bubbleText=text("设置"),customText=text("勘察员"),textareaText=text("请保留这段指令"),bubble=element(["bubble"],{},[bubbleText]),custom=element(["user-copy"],{},[customText]),input=element([], {placeholder:"输入密钥"}),textarea=element([],{placeholder:"描述这次要完成的任务…"},[textareaText],"textarea");'
            + 'const root=element([],{},[label,bubble,custom,input,textarea]);applyLanguage(root,"en");const english={lang:document.documentElement.lang,title:document.title,label:label.nodeValue,bubble:bubbleText.nodeValue,custom:customText.nodeValue,placeholder:input.getAttribute("placeholder"),textarea:textareaText.nodeValue,textareaPlaceholder:textarea.getAttribute("placeholder")};'
            + 'applyLanguage(root,"zh-CN");process.stdout.write(JSON.stringify({english,lang:document.documentElement.lang,title:document.title,label:label.nodeValue,placeholder:input.getAttribute("placeholder"),textarea:textareaText.nodeValue,textareaPlaceholder:textarea.getAttribute("placeholder")}));'
        )
        self.assertEqual(result["english"], {"lang": "en", "title": "Wu He Zhi Zhong", "label": "Settings", "bubble": "设置", "custom": "勘察员", "placeholder": "Enter API key", "textarea": "请保留这段指令", "textareaPlaceholder": "Describe the task to complete…"})
        self.assertEqual(result["lang"], "zh-CN")
        self.assertEqual(result["title"], "乌合之众")
        self.assertEqual(result["label"], "设置")
        self.assertEqual(result["placeholder"], "输入密钥")
        self.assertEqual(result["textarea"], "请保留这段指令")
        self.assertEqual(result["textareaPlaceholder"], "描述这次要完成的任务…")


if __name__ == "__main__":
    unittest.main()
