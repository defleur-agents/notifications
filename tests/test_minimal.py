import ast, json, subprocess, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def constants(path):
    result = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            try: result[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, AttributeError): pass
    return result
class MinimalInboxTests(unittest.TestCase):
    def test_skin_gate(self):
        gate = constants(ROOT/'mcp/minimal_assets.py')['MINIMAL_ASSETS']['layout.js'][1]
        for skin in ['obsidian','obsidian-light','landscapes','nebula','grove','tide','ember','signal']:
            for minimal in [False, True]:
                url = 'https://wizard.example/notifications/' + ('?layout=minimal' if minimal else '')
                code = 'const vm=require("node:vm");let result=null;const location={href:'+json.dumps(url)+',replace:u=>result=u};const window={WizardApp:{theme:{onChange:fn=>fn({skin:'+json.dumps(skin)+'})}}};vm.runInNewContext('+json.dumps(gate)+',{window,location,URL,document:{documentElement:{dataset:{}}}});console.log(JSON.stringify(result));'
                result = json.loads(subprocess.check_output(['node','-e',code]))
                wanted = skin in ['obsidian','obsidian-light','landscapes']
                self.assertEqual(result is not None, wanted != minimal)
                if result is not None: self.assertEqual('layout=minimal' in result, wanted)
    def test_original_screen_preserved(self):
        import hashlib
        const = constants(ROOT/'mcp/tools.py')
        baseline = json.loads((ROOT/'tests/original-screen-hashes.json').read_text())
        self.assertEqual({k:hashlib.sha256(const[k].encode()).hexdigest() for k in baseline},baseline)
    def test_chat_is_supported_unsent_not_url_or_send(self):
        source = (ROOT/'web/minimal/inbox.js').read_text()
        self.assertIn('WizardApp.nav.openContextDraft', source)
        self.assertNotIn('nav.openThread', source)
        self.assertNotIn('?session=', source)
        self.assertIn("receipt.sent !== false", source)
        self.assertNotIn('confirm(', source)
        self.assertIn('Confirm mark all read', source)
if __name__ == '__main__': unittest.main()
