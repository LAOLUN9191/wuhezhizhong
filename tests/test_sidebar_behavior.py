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


if __name__ == "__main__":
    unittest.main()
