"""Build ARC Chat.app on macOS: python3 macos/build.py."""
import json, pathlib, plistlib, shutil, subprocess
root=pathlib.Path(__file__).resolve().parent.parent
app=root/'dist/ARC Chat.app'
app.parent.mkdir(exist_ok=True)
subprocess.run(['osacompile','-o',str(app),str(root/'macos/app.applescript')],check=True)
resources=app/'Contents/Resources'
for name in ('helper.py','arc-chat.html','requirements.txt','config.py','state.py','model_providers.py','diagnostics.py','ood.py','workspace.py'):
    shutil.copy2(root/name,resources/name)
for name in ('launcher.py','launch.sh'):
    shutil.copy2(root/'macos'/name,resources/name)
(resources/'runtime.json').write_text(json.dumps({'existing_runtime':str(root)}))
p=app/'Contents/Info.plist'
d=plistlib.loads(p.read_bytes())
d.update(CFBundleIdentifier='edu.research.arc-chat.local',CFBundleName='ARC Chat',CFBundleDisplayName='ARC Chat',CFBundleShortVersionString='0.2.0',NSHighResolutionCapable=True)
p.write_bytes(plistlib.dumps(d))
subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
print(app)
