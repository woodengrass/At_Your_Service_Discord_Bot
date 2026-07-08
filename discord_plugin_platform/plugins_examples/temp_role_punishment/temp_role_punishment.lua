local MAX_DURATION_SECONDS = 7 * 24 * 60 * 60
local RESTORE_TASK_NAME = "restore_member_roles"

local function get_option(options, name)
    if options == nil then
        return nil
    end
    for _, option in ipairs(options) do
        if option.name == name then
            return option.value
        end
    end
    return nil
end

local function to_number(value)
    if value == nil then
        return nil
    end
    return tonumber(value)
end

local function send_usage(channel_id)
    api.send_message(
        channel_id,
        "用法：/temp_role user_id:<成員ID> temporary_role_id:<臨時身分組ID> duration_seconds:<秒數>"
    )
end

function on_slash_command(payload)
    if payload.command_name ~= "temp_role" then
        return
    end

    local user_id = to_number(get_option(payload.options, "user_id"))
    local temporary_role_id = to_number(get_option(payload.options, "temporary_role_id"))
    local duration_seconds = to_number(get_option(payload.options, "duration_seconds"))

    if user_id == nil or temporary_role_id == nil or duration_seconds == nil then
        send_usage(payload.channel_id)
        return
    end

    if duration_seconds <= 0 or duration_seconds > MAX_DURATION_SECONDS then
        api.send_message(payload.channel_id, "duration_seconds 必須介於 1 秒到 7 天之間。")
        return
    end

    local original_role_ids = api.get_member_role_ids(user_id)
    if original_role_ids == nil then
        api.send_message(payload.channel_id, "找不到指定成員，無法調整身分組。")
        return
    end

    for _, role_id in ipairs(original_role_ids) do
        if role_id ~= temporary_role_id then
            api.remove_role(user_id, role_id)
        end
    end
    api.add_role(user_id, temporary_role_id)

    api.schedule_task(duration_seconds, RESTORE_TASK_NAME, {
        user_id = user_id,
        channel_id = payload.channel_id,
        temporary_role_id = temporary_role_id,
        original_role_ids = original_role_ids,
    })

    api.send_message(payload.channel_id, "已套用臨時身分組，時間到後會自動還原。")
end

function on_scheduled_task(payload)
    if payload.task_name ~= RESTORE_TASK_NAME then
        return
    end

    local task_payload = payload.payload
    if task_payload == nil or task_payload.user_id == nil then
        return
    end

    api.remove_role(task_payload.user_id, task_payload.temporary_role_id)
    for _, role_id in ipairs(task_payload.original_role_ids or {}) do
        api.add_role(task_payload.user_id, role_id)
    end

    if task_payload.channel_id ~= nil then
        api.send_message(task_payload.channel_id, "臨時身分組已到期，已還原原本身分組。")
    end
end
