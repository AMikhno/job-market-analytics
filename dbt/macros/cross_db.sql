{# Case-insensitive word-boundary regex match, dispatched per warehouse so the
   same model runs on DuckDB (dev) and BigQuery (prod). #}

{% macro regexp_word_ci(column, term) -%}
    {{ return(adapter.dispatch('regexp_word_ci', 'job_pipeline')(column, term)) }}
{%- endmacro %}

{% macro default__regexp_word_ci(column, term) -%}
    regexp_matches(lower({{ column }}), '\b' || lower({{ term }}) || '\b')
{%- endmacro %}

{% macro bigquery__regexp_word_ci(column, term) -%}
    regexp_contains({{ column }}, r'(?i)\b' || {{ term }} || r'\b')
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
