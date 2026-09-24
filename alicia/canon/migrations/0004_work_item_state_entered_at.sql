-- REV-522: WorkItem.state_entered_at lives in the existing JSON data column.
-- Existing transitioned records use their final transition timestamp.  A
-- legacy initial-state record has no reliable historical entry, so it remains
-- null and the reporting view explicitly renders its age as unknown.
UPDATE work_items
SET data = json_set(
    data,
    '$.state_entered_at',
    CASE
        WHEN json_array_length(COALESCE(json_extract(data, '$.state_history'), '[]')) > 0
        THEN json_extract(data, '$.state_history[#-1].time')
        ELSE json('null')
    END
)
WHERE json_type(data, '$.state_entered_at') IS NULL;
