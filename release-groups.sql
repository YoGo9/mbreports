WITH rg_counts AS MATERIALIZED (
    SELECT
        rg.id,
        rg.gid,
        rg.name,
        rg.artist_credit,
        COUNT(r.id)::integer AS release_count
    FROM musicbrainz.release_group rg
    JOIN musicbrainz.release r
        ON r.release_group = rg.id
    GROUP BY
        rg.id,
        rg.gid,
        rg.name,
        rg.artist_credit
    HAVING COUNT(r.id) >= 5
),
credits AS MATERIALIZED (
    SELECT DISTINCT artist_credit
    FROM rg_counts
),
credit_artists AS (
    SELECT
        acn.artist_credit,
        jsonb_agg(
            jsonb_build_object(
                'mbid', a.gid,
                'name', a.name,
                'credited_name', acn.name,
                'area_mbid', ar.gid,
                'area', ar.name
            )
            ORDER BY acn.position
        ) AS artists
    FROM credits c
    JOIN musicbrainz.artist_credit_name acn
        ON acn.artist_credit = c.artist_credit
    JOIN musicbrainz.artist a
        ON a.id = acn.artist
    LEFT JOIN musicbrainz.area ar
        ON ar.id = a.area
    GROUP BY acn.artist_credit
),
report_rows AS (
    SELECT
        rg.gid AS release_group_mbid,
        rg.name AS release_group,
        ac.gid AS artist_credit_mbid,
        ac.name AS artist_credit,
        rg.release_count,
        ca.artists
    FROM rg_counts rg
    JOIN musicbrainz.artist_credit ac
        ON ac.id = rg.artist_credit
    LEFT JOIN credit_artists ca
        ON ca.artist_credit = rg.artist_credit
)
SELECT jsonb_build_object(
    'report', 'Release Groups With the Most Releases',
    'minimum_releases', 5,
    'generated_at', NOW(),
    'count', (SELECT COUNT(*) FROM report_rows),
    'data',
    (
        SELECT jsonb_agg(
            to_jsonb(report_rows)
            ORDER BY release_count DESC, release_group
        )
        FROM report_rows
    )
);
