import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIDEBAR_SOURCE = (PROJECT_ROOT / "laowu-sidebar.html").read_text(encoding="utf-8")


class SidebarCredentialTests(unittest.TestCase):
    def test_free_role_is_labeled_and_described_as_general_non_code_work(self):
        self.assertIn('free:"通用非代码工作：处理研究、整理、写作、分析及其他非代码任务；除非明确要求编程，否则不检查或修改源代码。"', SIDEBAR_SOURCE)
        self.assertIn('free:"自由者"', SIDEBAR_SOURCE)
        self.assertIn('profileRoleLabel(profile.role)', SIDEBAR_SOURCE)

    def test_removing_last_provider_reference_uses_inline_key_deletion_confirmation(self):
        start = SIDEBAR_SOURCE.index('else if(button.dataset.removeGroup)')
        end = SIDEBAR_SOURCE.index('else if(button.dataset.addGroup)', start)
        removal = SIDEBAR_SOURCE[start:end]
        self.assertIn('pendingProviderDeletes.add(removed.providerId)', removal)
        self.assertIn('pendingGroupDeleteProviderId!==removed.providerId', removal)
        self.assertIn('button.textContent="确认删除分组"', removal)
        self.assertIn('再次点击后删除分组并清除本机加密密钥', removal)
        self.assertNotIn('window.confirm', removal)
        self.assertLess(removal.index('pendingGroupDeleteProviderId=removed.providerId'), removal.index('pendingProviderDeletes.add'))
        self.assertIn('if(losesAuto&&route.nativeCodexFallback!==true)route.enabled=false', removal)

    def test_group_order_has_keyboard_buttons_and_saves_route_draft(self):
        self.assertIn('data-move-group-up="${esc(route.id)}:${groupIndex}"', SIDEBAR_SOURCE)
        self.assertIn('data-move-group-down="${esc(route.id)}:${groupIndex}"', SIDEBAR_SOURCE)
        self.assertIn('data-move-group-up="${esc(route.id)}:${groupIndex}" aria-label="上移', SIDEBAR_SOURCE)
        self.assertIn('title="上移" ${groupIndex===0?"disabled":""}', SIDEBAR_SOURCE)
        self.assertIn('data-move-group-down="${esc(route.id)}:${groupIndex}" aria-label="下移', SIDEBAR_SOURCE)
        self.assertIn('title="下移" ${(route.groups||[]).length-1===groupIndex?"disabled":""}', SIDEBAR_SOURCE)
        self.assertIn('markRouteDraftChanged();renderConfiguration();void saveRouteSettings({automatic:true});', SIDEBAR_SOURCE)
        self.assertIn('.drag-handle', SIDEBAR_SOURCE)

    def test_global_concurrency_control_is_removed(self):
        self.assertNotIn('id="subagent-concurrency"', SIDEBAR_SOURCE)
        self.assertNotIn('action:"concurrency"', SIDEBAR_SOURCE)
        self.assertNotIn('data-builtin-concurrency', SIDEBAR_SOURCE)
        self.assertNotIn('builtin_concurrency', SIDEBAR_SOURCE)
        self.assertIn('.shell{height:100%;display:grid;grid-template-rows:52px minmax(0,1fr)}', SIDEBAR_SOURCE)
        self.assertIn('@media(max-width:520px){.body.list-collapsed{grid-template-rows:minmax(0,1fr)}.shell{grid-template-rows:48px minmax(0,1fr)}', SIDEBAR_SOURCE)
        self.assertIn('.brand{font-family:"宋体",SimSun,serif;', SIDEBAR_SOURCE)
        self.assertIn('.brand-mark{display:grid;place-items:center;width:32px;height:32px;border-radius:10px;background:linear-gradient(145deg,#9d81ff,#65b6ff);', SIDEBAR_SOURCE)

    def test_profile_list_has_no_role_concurrency_handler(self):
        self.assertNotIn('const concurrencyInput=event.target.closest("[data-builtin-concurrency]")', SIDEBAR_SOURCE)
        self.assertNotIn('data-builtin-concurrency-status', SIDEBAR_SOURCE)

    def test_activity_styles_use_appearance_accent_and_theme_backgrounds(self):
        self.assertRegex(SIDEBAR_SOURCE, r"\.entry\.assistant \.bubble\{[^}]*border-left-color:var\(--button-text,var\(--line\)\)")
        self.assertRegex(SIDEBAR_SOURCE, r"\.state-completed,\.state-failed,\.chip\.primary\{[^}]*border:1px solid var\(--button-text,var\(--line\)\)")
        self.assertRegex(SIDEBAR_SOURCE, r"\.top\{[^}]*background:var\(--panel\)")
        self.assertRegex(SIDEBAR_SOURCE, r"\.detail>\.meta\{[^}]*background:var\(--bg\)")
        self.assertIn(".brand-mark{display:grid;place-items:center;width:32px;height:32px;border-radius:10px;background:linear-gradient(145deg,#9d81ff,#65b6ff);color:#11131a;font-weight:800;box-shadow:0 5px 18px #8d7bff30}", SIDEBAR_SOURCE)

    def test_scrollbar_widths_cover_activity_settings_mobile_and_transcript(self):
        self.assertIn(".body.settings-open::-webkit-scrollbar{width:2px;height:2px}", SIDEBAR_SOURCE)
        self.assertIn(".list::-webkit-scrollbar{width:2px;height:2px}", SIDEBAR_SOURCE)
        self.assertIn(".agents::-webkit-scrollbar{display:block;height:2px}", SIDEBAR_SOURCE)
        self.assertIn(".transcript::-webkit-scrollbar{width:2px;height:2px}", SIDEBAR_SOURCE)
        self.assertIn(".transcript::-webkit-scrollbar-track{background:transparent}", SIDEBAR_SOURCE)
        self.assertIn(".transcript::-webkit-scrollbar-thumb{border-radius:999px;background:var(--line)}", SIDEBAR_SOURCE)
        self.assertIn("scrollbar-width:thin", SIDEBAR_SOURCE)

    def test_activity_conversation_borders_use_appearance_accent(self):
        self.assertRegex(SIDEBAR_SOURCE, r"\.entry\.user \.bubble\{[^}]*border-left-color:var\(--button-text,var\(--line\)\)")
        self.assertRegex(SIDEBAR_SOURCE, r"\.entry\.user \.event-marker,\.entry\.assistant \.event-marker\{[^}]*border-color:var\(--button-text,var\(--line\)\)")

    def test_eye_button_loads_saved_key_before_revealing_it(self):
        start = SIDEBAR_SOURCE.index("function toggleKeyVisibility(button)")
        end = SIDEBAR_SOURCE.index("async function saveGroup(", start)
        toggle = SIDEBAR_SOURCE[start:end]

        self.assertIn('name:"laowu_reveal_credential"', toggle)
        self.assertIn("input.value=data.key", toggle)

    def test_revealed_key_comes_from_private_tool_metadata(self):
        start = SIDEBAR_SOURCE.index("function toggleKeyVisibility(button)")
        end = SIDEBAR_SOURCE.index("async function saveGroup(", start)
        toggle = SIDEBAR_SOURCE[start:end]

        self.assertIn(
            "[result,result?.result,result?.mcp_tool_result,result?.call_tool_result].map(item=>item?._meta?.laowu)",
            toggle,
        )
        self.assertNotIn("const data=textOf(result);", toggle)

    def test_rpc_ignores_messages_not_sent_by_the_host_window(self):
        start = SIDEBAR_SOURCE.index("function receive(event)")
        end = SIDEBAR_SOURCE.index('window.addEventListener("message",receive)', start)
        receive = SIDEBAR_SOURCE[start:end]

        self.assertIn("event.source!==window.parent", receive)
        self.assertIn('msg.jsonrpc!=="2.0"', receive)

    def test_refresh_preserves_route_draft_when_autosave_fails(self):
        start = SIDEBAR_SOURCE.index("async function loadConfiguration()")
        end = SIDEBAR_SOURCE.index("function cloneRoutes(", start)
        loader = SIDEBAR_SOURCE[start:end]

        self.assertIn("if(!saved){", loader)
        self.assertIn("routeDraft&&routeDraftRevision!==savedRouteDraftRevision", loader)
        save_failure = loader.index("if(!saved){")
        dirty_guard = loader.index("routeDraft&&routeDraftRevision!==savedRouteDraftRevision")
        reload_draft = loader.index("fetchConfiguration({resetDraft:true})")
        save_failure_branch = loader[save_failure:dirty_guard]
        self.assertLess(save_failure, reload_draft)
        self.assertLess(dirty_guard, reload_draft)
        self.assertIn("renderConfiguration()", save_failure_branch)
        self.assertLess(save_failure_branch.index("renderConfiguration()"), save_failure_branch.index("return;"))

    def test_model_query_requires_the_saved_provider_address(self):
        start = SIDEBAR_SOURCE.index("async function queryModels(providerId,button)")
        end = SIDEBAR_SOURCE.index("async function saveGroup(", start)
        query = SIDEBAR_SOURCE[start:end]

        self.assertIn("configuration.availableGroups", query)
        self.assertIn("savedBaseUrl", query)
        self.assertIn("baseUrl!==savedBaseUrl", query)

    def test_route_native_codex_fallback_control_is_available(self):
        self.assertIn("data-native-codex-fallback", SIDEBAR_SOURCE)
        self.assertNotIn("允许主控调用", SIDEBAR_SOURCE)

    def test_provider_config_copy_button_and_clipboard_handler_are_removed(self):
        self.assertNotIn('data-copy-provider-config=', SIDEBAR_SOURCE)
        self.assertNotIn("复制 Codex 配置", SIDEBAR_SOURCE)
        self.assertNotIn("function copyProviderConfig(", SIDEBAR_SOURCE)
        self.assertNotIn("navigator.clipboard.writeText", SIDEBAR_SOURCE)

    def test_route_card_disable_dims_and_disables_its_groups(self):
        self.assertTrue('data-route-card="${esc(route.id)}" class="platform-card ${route.enabled===false?"route-disabled":""}"' in SIDEBAR_SOURCE, "route card gets a disabled style")
        self.assertTrue("${route.enabled===false||group.enabled===false?\"disabled\":\"\"}" in SIDEBAR_SOURCE, "route closure marks every nested group disabled")
        self.assertTrue(".platform-card.route-disabled" in SIDEBAR_SOURCE, "closed route card has a muted style")
        self.assertTrue('data-add-group="${esc(route.id)}" ${route.enabled===false||addingGroups.has(route.id)?"disabled":""}' in SIDEBAR_SOURCE, "closed or pending route cannot add a group")

    def test_group_builder_has_separator_from_existing_group_cards(self):
        self.assertTrue(".group-builder{display:flex;gap:6px;margin-top:4px;padding-top:4px;border-top:1px solid var(--line)" in SIDEBAR_SOURCE, "compact group creation area remains separated")
        self.assertIn(".platform-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:14px}", SIDEBAR_SOURCE)
        self.assertIn(".platform-grid>[data-route-card]{min-width:0}", SIDEBAR_SOURCE)
        self.assertIn(".platform-grid{grid-template-columns:minmax(0,1fr)}", SIDEBAR_SOURCE)

    def test_sidebar_width_minimum_is_200_pixels(self):
        self.assertIn("const min = 200;", SIDEBAR_SOURCE)
        self.assertIn('id="sidebar-size-slider" type="range" min="200"', SIDEBAR_SOURCE)
        self.assertIn("grid-template-columns:var(--sidebar-width,minmax(200px,250px))", SIDEBAR_SOURCE)

    def test_model_query_waits_longer_and_reports_timeout_clearly(self):
        self.assertIn('name:"laowu_query_models",arguments:args},60000)', SIDEBAR_SOURCE)
        self.assertIn("等待 Codex 响应超时，请稍后重试。", SIDEBAR_SOURCE)

    def test_new_timeline_markers_animate_and_respect_motion_setting(self):
        self.assertIn("@keyframes timeline-marker-in", SIDEBAR_SOURCE)
        self.assertIn(".entry.marker-arrive .event-marker", SIDEBAR_SOURCE)
        self.assertIn('if(animationsEnabled)detail.querySelectorAll(".entry[data-event-id]")', SIDEBAR_SOURCE)
        self.assertIn('body.motion-off *,body.motion-off *::before,body.motion-off *::after{animation:none!important', SIDEBAR_SOURCE)

    def test_profile_select_hides_disabled_route_groups_and_shows_stale_warning(self):
        start = SIDEBAR_SOURCE.index("function profileRouteGroupMarkup(profile)")
        end = SIDEBAR_SOURCE.index("function renderProfiles()", start)
        markup = SIDEBAR_SOURCE[start:end]
        self.assertTrue("option.enabled!==false" in markup, "disabled routes are omitted from selectable options")
        self.assertTrue("已固定路线或分组已关闭" in markup, "stale assignment is explained outside the select")
        self.assertTrue("options.push({value:current" not in markup, "disabled saved route is not inserted as an option")

    def test_profile_configuration_hides_removed_permission_toggles(self):
        start = SIDEBAR_SOURCE.index("function profileEditorMarkup()")
        end = SIDEBAR_SOURCE.index("function showSettingsView()", start)
        profile_page = SIDEBAR_SOURCE[start:end]
        for value in ("profile-callable-input", "profile-allow-mcp-tools-input", "profile-allow-skills-input", "data-profile-permission", "data-profile-allow-mcp-tools", "data-profile-allow-skills"):
            self.assertNotIn(value, profile_page)

    def test_new_profile_form_omits_role_and_creates_as_scout(self):
        start = SIDEBAR_SOURCE.index("function profileEditorMarkup()")
        end = SIDEBAR_SOURCE.index("async function loadProfiles()", start)
        editor = SIDEBAR_SOURCE[start:end]
        self.assertIn('${profile?`<div class="form-row"><label for="profile-role-input">角色</label>', editor)
        self.assertIn('role:profile?.role||"scout"', SIDEBAR_SOURCE)

    def test_profile_configuration_has_no_removed_capability_controls(self):
        start = SIDEBAR_SOURCE.index("function profileEditorMarkup()")
        end = SIDEBAR_SOURCE.index("function showSettingsView()", start)
        self.assertNotIn("builtin_capabilities", SIDEBAR_SOURCE[start:end])
        self.assertNotIn("profileCapabilityError", SIDEBAR_SOURCE[start:end])

    def test_route_settings_offer_native_codex_fallback(self):
        self.assertIn("native_codex_fallback:route.nativeCodexFallback===true", SIDEBAR_SOURCE)
        self.assertNotIn("原生子代理设置未被 MCP 服务保存", SIDEBAR_SOURCE)

    def test_appearance_and_codex_permission_share_a_compact_card(self):
        self.assertIn('class="appearance-preferences"', SIDEBAR_SOURCE)
        self.assertIn('class="permission-control" title="', SIDEBAR_SOURCE)
        self.assertIn('id="allow-codex-launch"', SIDEBAR_SOURCE)
        self.assertIn('class="theme-control-wrap"', SIDEBAR_SOURCE)
        self.assertIn(".appearance-preferences{", SIDEBAR_SOURCE)




    def test_completed_activity_shows_master_recall_only_when_available_without_relaunch_ui(self):
        start = SIDEBAR_SOURCE.index("function render()")
        end = SIDEBAR_SOURCE.index("async function initialize()", start)
        render = SIDEBAR_SOURCE[start:end]

        self.assertIn('activity.status==="completed"&&activity.masterRecallAvailable===true', render)
        self.assertIn('继续对话', render)
        self.assertNotIn('再次发起子代理', render)
        self.assertNotIn('data-master-recall', render)



    def test_reply_expand_toggle_is_hidden_when_full_reply_fits_and_rechecked_after_resize(self):
        self.assertIn("function syncReplyToggles()", SIDEBAR_SOURCE)
        self.assertIn("const fullHeight=bubble.getBoundingClientRect().height", SIDEBAR_SOURCE)
        self.assertIn("const collapsedHeight=bubble.getBoundingClientRect().height", SIDEBAR_SOURCE)
        self.assertIn("expandedReplyIds.delete(replyId)", SIDEBAR_SOURCE)
        self.assertIn("window.addEventListener(\"resize\",syncReplyToggles)", SIDEBAR_SOURCE)
        self.assertIn(".reply-toggle[hidden]{display:none}", SIDEBAR_SOURCE)



class SidebarActivityTests(unittest.TestCase):
    def test_event_toggle_is_bound_once(self):
        start = SIDEBAR_SOURCE.index('detail.querySelectorAll("[data-event-toggle]")')
        end = SIDEBAR_SOURCE.index('requestAnimationFrame(syncReplyToggles)', start)
        binding = SIDEBAR_SOURCE[start:end]

        self.assertIn("button._eventToggleBound", binding)
        self.assertIn("button._eventToggleBound=true", binding)

    def test_context_usage_is_not_rendered_in_activity_detail(self):
        render_start = SIDEBAR_SOURCE.index("function render()")
        render_end = SIDEBAR_SOURCE.index("async function initialize()", render_start)
        render = SIDEBAR_SOURCE[render_start:render_end]

        self.assertNotIn("activity.contextUsage", render)
        self.assertNotIn("context-usage-panel", render)
        self.assertNotIn("上下文占用", render)

    def test_timeline_uses_single_caller_label_and_hides_tool_speaker(self):
        render_start = SIDEBAR_SOURCE.index("function render()")
        render_end = SIDEBAR_SOURCE.index("async function initialize()", render_start)
        render = SIDEBAR_SOURCE[render_start:render_end]

        self.assertIn('if(kind==="user"&&title==="任务") { title=activity.initiator==="user"?"用户 调用":"Codex 调用"; speaker=""; }', render)
        self.assertIn('else if(kind==="user"&&title==="补充指令") { title="Codex 调用"; speaker=""; }', render)
        self.assertNotIn('kind==="tool"?"工具"', render)

    def test_completed_activity_does_not_reopen_launch_form_for_recall(self):
        render_start = SIDEBAR_SOURCE.index("function render()")
        render_end = SIDEBAR_SOURCE.index("async function initialize()", render_start)
        render = SIDEBAR_SOURCE[render_start:render_end]
        self.assertIn('activity.status==="completed"&&activity.masterRecallAvailable===true', render)
        self.assertIn("laowu_continue_task", render)
        self.assertNotIn('data-activity-action="recall"', render)
        self.assertNotIn('showLaunchView(activity.profileId||`builtin-${activity.role}`', render)
        self.assertNotIn("showLaunchView", SIDEBAR_SOURCE)
        self.assertNotIn("id=\"launch-task\"", SIDEBAR_SOURCE)

    def test_session_list_can_collapse_and_persist(self):
        self.assertIn('id="toggle-activity-list"', SIDEBAR_SOURCE)
        self.assertIn('aria-expanded="true"', SIDEBAR_SOURCE)
        self.assertIn('laowu-activity-list-collapsed', SIDEBAR_SOURCE)
        self.assertIn("list-collapsed", SIDEBAR_SOURCE)
        self.assertIn("activity-splitter", SIDEBAR_SOURCE)

    def test_activity_uses_short_title_and_current_group_label(self):
        self.assertIn("function activityTitle(activity)", SIDEBAR_SOURCE)
        self.assertIn("activity.currentGroup", SIDEBAR_SOURCE)
        self.assertIn('title="${esc(activity.task)}"', SIDEBAR_SOURCE)

    def test_long_assistant_replies_can_expand_and_collapse(self):
        self.assertIn("-webkit-line-clamp:6", SIDEBAR_SOURCE)
        self.assertIn("expandedReplyIds", SIDEBAR_SOURCE)
        self.assertIn("eventId", SIDEBAR_SOURCE)
        self.assertIn("展开", SIDEBAR_SOURCE)
        self.assertIn("收起", SIDEBAR_SOURCE)

    def test_tool_and_progress_events_default_collapsed_with_right_accessible_toggle(self):
        self.assertIn('const expandedEventIds = new Set()', SIDEBAR_SOURCE)
        self.assertIn('const expandableEvent=kind==="tool"||kind==="progress"', SIDEBAR_SOURCE)
        self.assertIn('expandableEvent&&!eventExpanded?" event-collapsed":""', SIDEBAR_SOURCE)
        self.assertIn('class="event-toggle" data-event-toggle="${esc(replyId)}" aria-expanded="${eventExpanded}"', SIDEBAR_SOURCE)
        self.assertIn('aria-label="${eventExpanded?"收起":"展开"}详情"', SIDEBAR_SOURCE)
        self.assertIn('entry-head', SIDEBAR_SOURCE)
        self.assertIn('expandedEventIds.has(replyId)', SIDEBAR_SOURCE)
        self.assertIn('expandedEventIds.delete(id)', SIDEBAR_SOURCE)
        self.assertIn('expandedEventIds.add(id)', SIDEBAR_SOURCE)
        self.assertIn('if(expansionActivityId!==activity.id){expandedEventIds.clear()', SIDEBAR_SOURCE)

    def test_interaction_controls_and_calls_are_removed(self):
        self.assertNotIn("laowu_interact", SIDEBAR_SOURCE)
        self.assertNotIn("launch-interactive", SIDEBAR_SOURCE)
        self.assertNotIn("interactionMarkup", SIDEBAR_SOURCE)
        self.assertNotIn("interaction-input", SIDEBAR_SOURCE)
        self.assertNotIn("pendingQuestion", SIDEBAR_SOURCE)
        self.assertNotIn("composer", SIDEBAR_SOURCE)
        self.assertNotIn("interactive:", SIDEBAR_SOURCE)

    def test_refresh_ignores_stale_snapshot_results(self):
        self.assertIn("refreshInFlight", SIDEBAR_SOURCE)
        self.assertIn("refreshRevision", SIDEBAR_SOURCE)
        self.assertIn("if(revision!==refreshRevision)return", SIDEBAR_SOURCE)

    def test_render_creates_activity_detail_without_removed_interaction_references(self):
        start = SIDEBAR_SOURCE.index("function render()")
        end = SIDEBAR_SOURCE.index("async function initialize()", start)
        render = SIDEBAR_SOURCE[start:end]

        self.assertRegex(render, r"if\(activityChanged\)\s*\{\s*detail\.innerHTML")
        activity_branch = render[render.index("if(activityChanged) {"):]
        detail_write = activity_branch.index("detail.innerHTML=")
        transcript_access = activity_branch.index('const transcript=detail.querySelector(".transcript")')
        inserted_detail = activity_branch[detail_write:transcript_access]
        self.assertLess(detail_write, transcript_access)
        self.assertIn("${metaHtml}", inserted_detail)
        self.assertIn('class="transcript"', inserted_detail)
        self.assertIn('detail.querySelector(".timeline").innerHTML=', render)
        self.assertNotIn("interactionSnapshot", render)
        self.assertNotIn("bindInteraction(", render)

    def test_activity_list_toggle_is_bound_after_activity_layout_is_rebuilt(self):
        toggle_start = SIDEBAR_SOURCE.index("function bindActivityListToggle()")
        toggle_end = SIDEBAR_SOURCE.index("function saveSidebarWidth()", toggle_start)
        toggle_helper = SIDEBAR_SOURCE[toggle_start:toggle_end]
        self.assertIn('button.addEventListener("click",toggleActivityList)', toggle_helper)
        show_start = SIDEBAR_SOURCE.index("function showActivityView()")
        show_end = SIDEBAR_SOURCE.index("async function showProfilesView()", show_start)
        show_activity = SIDEBAR_SOURCE[show_start:show_end]
        self.assertLess(
            show_activity.index("root.innerHTML = activityLayoutMarkup()"),
            show_activity.index("bindActivityListToggle();"),
        )
        render_start = SIDEBAR_SOURCE.index("function render()")
        render_end = SIDEBAR_SOURCE.index("async function initialize()", render_start)
        render = SIDEBAR_SOURCE[render_start:render_end]
        no_activity = render[render.index("if(!activity)"):render.index("const previousTranscript")]
        self.assertIn('class="detail-list-toggle"', no_activity)
        self.assertLess(no_activity.index("detail.innerHTML="), no_activity.index("bindActivityListToggle();"))
        meta_start = render.index("const metaHtml=")
        activity_start = render.index("if(activityChanged) {")
        meta_html = render[meta_start:activity_start]
        activity_end = render.index('const transcript=detail.querySelector(".transcript")', activity_start)
        self.assertIn('class="detail-list-toggle"', meta_html)
        self.assertIn("${metaHtml}", render[activity_start:activity_end])

    def test_collapsed_detail_toggle_stays_in_the_metadata_grid_row(self):
        self.assertIn(
            ".detail{display:grid;grid-template-rows:auto minmax(0,1fr)",
            SIDEBAR_SOURCE,
        )
        meta_start = SIDEBAR_SOURCE.index("const metaHtml=")
        meta_end = SIDEBAR_SOURCE.index("if(activityChanged)", meta_start)
        self.assertIn('class="meta"><button class="detail-list-toggle"', SIDEBAR_SOURCE[meta_start:meta_end])

    def test_detail_toggle_rebinds_after_metadata_is_replaced(self):
        start = SIDEBAR_SOURCE.index("function render()")
        end = SIDEBAR_SOURCE.index("async function initialize()", start)
        render = SIDEBAR_SOURCE[start:end]
        activity_branch = render.index("if(activityChanged) {")
        metadata_update = render.index("replaceWith(new DOMParser()", activity_branch)
        next_snapshot = render[metadata_update:render.index('const transcript=detail.querySelector(".transcript")', metadata_update)]

        self.assertIn("bindActivityListToggle();", next_snapshot)
        self.assertLess(next_snapshot.index("replaceWith("), next_snapshot.index("bindActivityListToggle();"))

    def test_initialization_error_keeps_list_expand_control(self):
        start = SIDEBAR_SOURCE.index("async function initialize()")
        end = SIDEBAR_SOURCE.index("initialize();", start)
        initialize = SIDEBAR_SOURCE[start:end]
        catch_start = initialize.rindex("} catch(error)")
        error_path = initialize[catch_start:]

        self.assertIn('class="detail-list-toggle"', error_path)
        self.assertLess(error_path.index('document.getElementById("detail").innerHTML='), error_path.index("bindActivityListToggle();"))

    def test_refresh_error_keeps_and_binds_list_toggle(self):
        start = SIDEBAR_SOURCE.index("async function refresh()")
        end = SIDEBAR_SOURCE.index("function statusLabel(", start)
        refresh = SIDEBAR_SOURCE[start:end]
        catch_start = refresh.index("} catch(error)")
        error_path = refresh[catch_start:]

        self.assertIn('class="detail-list-toggle"', error_path)
        self.assertLess(error_path.index("detail.innerHTML="), error_path.index("bindActivityListToggle();"))


if __name__ == "__main__":
    unittest.main()
