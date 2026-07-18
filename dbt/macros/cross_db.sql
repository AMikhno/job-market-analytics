{# Case-insensitive whole-word match of a seed term inside a text column,
   dispatched per warehouse so the same model runs on DuckDB (dev) and
   BigQuery (prod).

   Seed terms are matched as literals, not regex fragments: every regex
   metacharacter in the term is escaped at query time, so terms like "C++",
   "C#", or ".NET" match exactly (an unescaped "." would make ".NET" hit
   "knet"). Word edges are explicit (string edge or [^a-z0-9]) rather than
   \b, because \b misfires on terms that end in a non-word character —
   "C++ " has no word boundary after '+'. One deliberate difference from
   \b: '_' counts as a word edge here, so "dbt" still matches "dbt_project". #}

{% macro regexp_word_ci(column, term) -%}
    {{ return(adapter.dispatch('regexp_word_ci', 'job_pipeline')(column, term)) }}
{%- endmacro %}

{# The inner regexp_replace backslash-escapes every regex metacharacter in the
   term (one character-class pass, '\1' backreference). Literal spelling note:
   DuckDB strings don't process backslash escapes, so '([\\...' is regex-escaped-
   backslash; BigQuery strings do, so its macro uses r'' raw strings to match. #}
{% macro default__regexp_word_ci(column, term) -%}
    regexp_matches(
        lower({{ column }}),
        '(^|[^a-z0-9])'
        || regexp_replace(lower({{ term }}), '([\\.+*?()\[\]{}|^$])', '\\\1', 'g')
        || '($|[^a-z0-9])'
    )
{%- endmacro %}

{% macro bigquery__regexp_word_ci(column, term) -%}
    regexp_contains(
        {{ column }},
        '(?i)(^|[^a-z0-9])'
        || regexp_replace({{ term }}, r'([\\.+*?()\[\]{}|^$])', r'\\\1')
        || '($|[^a-z0-9])'
    )
{%- endmacro %}

{# `hours` before a timestamp expression. Not dbt.dateadd: its BigQuery
   implementation returns DATETIME, which cannot be compared to a TIMESTAMP
   column (a prod-only type error the DuckDB dev target would never catch).
   `hours` must render to an integer literal (e.g. a var()) - BigQuery
   INTERVAL syntax does not take a column reference here. #}
{% macro timestamp_hours_before(ts, hours) -%}
    {{ return(adapter.dispatch('timestamp_hours_before', 'job_pipeline')(ts, hours)) }}
{%- endmacro %}

{% macro default__timestamp_hours_before(ts, hours) -%}
    {{ ts }} - interval {{ hours }} hour
{%- endmacro %}

{% macro bigquery__timestamp_hours_before(ts, hours) -%}
    timestamp_sub({{ ts }}, interval {{ hours }} hour)
{%- endmacro %}

{# Strip HTML tags for keyword matching. DuckDB's regexp_replace replaces only the
   first match without the 'g' flag; BigQuery's is global by default. #}
{% macro strip_html(column) -%}
    {{ return(adapter.dispatch('strip_html', 'job_pipeline')(column)) }}
{%- endmacro %}

{% macro default__strip_html(column) -%}
    regexp_replace({{ column }}, '<[^>]+>', ' ', 'g')
{%- endmacro %}

{% macro bigquery__strip_html(column) -%}
    regexp_replace({{ column }}, '<[^>]+>', ' ')
{%- endmacro %}
