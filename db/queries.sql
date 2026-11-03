-- Latest legal publications. No subscriber identifiers are selected.
SELECT f.created_at, f.entity AS topic, f.summary, m.url, s.name AS source
FROM legal_findings f
JOIN materials m ON m.id=f.material_id
JOIN sources s ON s.id=m.source_id
ORDER BY f.created_at DESC LIMIT 20;

-- Source availability and most recent successful check.
SELECT name, active, last_success_at, last_error FROM sources ORDER BY name;

-- Alternate links grouped under one publication.
SELECT material_id, count(*) AS mentions FROM material_mentions
GROUP BY material_id HAVING count(*) > 1;
