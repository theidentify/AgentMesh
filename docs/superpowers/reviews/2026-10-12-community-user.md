อ่านตั้งแต่ `docs/introduction.md` ตามลำดับ sidebar ไปจนถึง `docs/reference/index.md` รวมข้อความในภาพประกอบแล้ว ไม่ได้เปิดซอร์สโค้ด ไม่ได้รันติดตั้ง และไม่ได้แก้ไฟล์ รายงานนี้วิจารณ์สิ่งที่คนอ่านคู่มือรู้และทำต่อได้ ไม่ได้ยืนยันว่าโปรแกรมมีบั๊ก ส่วนที่โยนไปเอกสารอ้างอิง ผมระบุว่าเป็นทางแยกออกจากคู่มือ ไม่ได้เหมาว่าไม่มีคำตอบอยู่ที่อื่น

ความรู้สึกหลังอ่านคือ: **ผมเข้าใจแล้วว่าห้ามทำข้อมูลพังอย่างไร แต่ยังไม่เห็นเส้นทางครบตั้งแต่เจอ GitHub จนเครื่องสองเครื่องของผมจำเรื่องเดียวกันได้คืนนี้**

**1. จุดที่หลง ต้องเดา หรือไม่รู้จะทำอะไรต่อ — ตามลำดับที่อ่าน**

1. **หน้าแรกกับ sidebar บอกทางคนละทาง** — `docs/introduction.md:31`
   
   > Next, read [Concepts](./getting-started/concepts) to understand what stays local and what travels between devices.
   
   แต่ sidebar วาง Quick start ไว้ก่อน Concepts ที่ `docs/.vitepress/config.mts:23` ผมต้องเลือกเชื่ออันไหน? ยิ่ง `docs/introduction.md:11` แยกทางตามการมี bootstrap อีก เท่ากับเริ่มมาก็มีสามวิธีเดินแล้ว

2. **Quick start สำหรับคนที่มีของครบอยู่แล้ว ไม่ใช่คนเพิ่งเจอโปรเจกต์** — `docs/getting-started/quick-start.md:3`
   
   > Get from a supplied private bootstrap to your first memory recall.
   
   ใคร supplied ให้ผม? ยังดีที่บรรทัด 11 ส่งไป New sync group แต่ควรแยกตั้งแต่ต้นว่า “เริ่มคนเดียวจาก GitHub” กับ “ได้รับชุดติดตั้งจากคนดูแลกลุ่ม” ไม่ใช่ให้ผมอ่านแล้วค่อยพบว่ามาผิดประตู

3. **ให้ตรวจว่า identity ว่าง แต่ไม่บอกวิธีตรวจ** — `docs/getting-started/quick-start.md:8`
   
   > Confirm the installer's default device identity is unused before running it.
   
   ผมต้องถามใคร เปิดไฟล์ไหน หรือรันอะไร? ปัญหานี้ตามไปถึง `docs/getting-started/prerequisites.md:35` และหน้าติดตั้งทั้งสามระบบ ถ้าเลือกผิดมีผลหนัก คู่มือควรให้วิธีตรวจที่ทำได้จริง

4. **คำอธิบายระบบทำให้คิดว่า ingest อัตโนมัติทุกแบบ** — `docs/getting-started/concepts.md:11`
   
   > The **sync worker** picks up new sessions, stores them in the local database, **Local database**, and every 60 seconds exports local changes and imports changes from peers.
   
   อ่านแล้วผมเข้าใจว่าเปิด worker ก็เก็บบทสนทนา แต่กว่าจะถึง `docs/install/new-group.md:29` จึงรู้ว่ากลุ่มใหม่ปิด ingestion อยู่ ควรใส่เงื่อนไขตรงคำอธิบายครั้งแรก ไม่ใช่ให้ผมเปิดทิ้งแล้วสงสัยว่าทำไมไม่มีข้อมูล

5. **ชื่อ identity ชวนเลือกตาม OS แล้วกลับบอกว่าไม่ใช่ OS** — `docs/getting-started/concepts.md:19`
   
   > These are labels, not operating systems: a group supports at most three devices, each with a different identity.
   
   ข้อจำกัดสามเครื่องอธิบายแล้ว แต่ยังขาดตัวอย่างแจกช่องให้สอง Mac และหนึ่ง Linux รวมทั้งวิธีดูว่าแต่ละช่องเป็นของเครื่องไหน ถ้ามีเครื่องที่สี่ต้องทำอย่างไร หรือไม่มีทางในกลุ่มเดียว ควรบอกให้จบตรงนี้

6. **อยู่ดี ๆ PostgreSQL ก็โผล่มา** — `docs/getting-started/concepts.md:39`
   
   > Peer edits do not write back to PostgreSQL.
   
   เมื่อกี้บอกแต่ SQLite ผมต้องมี PostgreSQL ด้วยหรือเปล่า? ถ้าเป็นระบบเดิมหรือทางเลือกขั้นสูง ขอป้ายชัด ๆ ว่าคนติดตั้งใหม่ข้ามได้

7. **ตรวจ Python ได้ แต่ถ้าตรวจไม่ผ่านก็จบข่าว** — `docs/getting-started/prerequisites.md:11`
   
   > Install Python 3.10 or later with SQLite FTS5 and JSON support.
   
   มีคำสั่งตรวจ ถือว่าดี แต่ไม่มีทางติดตั้งที่แนะนำ ไม่มีตัวอย่างผล FTS5 ที่ถือว่าผ่าน และไม่มีทางแก้เมื่อ Python มีอยู่แต่ SQLite ขาดความสามารถนี้ ผมรู้ command line ไม่ได้แปลว่ารู้ว่า Python ชุดไหนผูก SQLite แบบไหน

8. **Syncthing ถูกย่อเหลือคำสั่งในอากาศ** — `docs/getting-started/prerequisites.md:27`
   
   > Install Syncthing separately, put `syncthing` on PATH, and keep it running.
   
   ไม่มีลิงก์ติดตั้ง ไม่มีวิธีเปิดหน้าจัดการ ไม่มีวิธีเช็กว่ารันอยู่ แล้วตอนเปิดเครื่องใหม่ใครเปิดมัน? คู่มือสอนให้ AgentMesh รันเบื้องหลังภายหลัง แต่ยังไม่ปิดงานฝั่ง Syncthing

9. **ขั้นตอนเชื่อมสองเครื่องหายไปทั้งก้อน** — `docs/getting-started/prerequisites.md:31`
   
   > Pair only trusted devices and accept the private exchange folder.
   
   ผมไม่เคยใช้ Syncthing จะเริ่ม pair ตรงไหน เอารหัสเครื่องจากไหน เครื่องไหนสร้าง folder เครื่องไหน share แล้วอีกเครื่องกด accept ตรงไหน? นี่คือหัวใจของการใช้หลายเครื่อง แต่ถูกเขียนเหมือนผมทำเป็นอยู่แล้ว

10. **ผมยังไม่มีใครส่งชุดติดตั้งให้** — `docs/getting-started/prerequisites.md:31`
    
    > For bootstrap installation, it must contain the supplied setup files, bootstrap ZIP and checksum manifest.
    
    ขอรายชื่อไฟล์จริงและตัวอย่างโครงสร้าง folder หน่อย “supplied setup files” ยังทำให้ผมไม่รู้ว่าของครบหรือขาด และ ZIP กับ manifest ที่เข้าคู่กันดูจากอะไร

11. **หน้าติดตั้ง macOS/Linux เริ่มกลางทาง** — `docs/install/macos.md:15` และ `docs/install/linux.md:15`
    
    > From the supplied setup directory:
    
    directory ไหนครับ? อยู่ใน exchange หรืออยู่ข้าง checkout? สองหน้านี้ให้รัน `setup_macos.sh` และ `setup_linux.sh` แต่ไม่พาผมไปถึงที่อยู่ของไฟล์ ขณะที่ Windows ระบุชัดกว่าให้เปิดใน exchange ที่ `docs/install/windows.md:19`

12. **เปลี่ยน identity ได้ แต่ตัวอย่าง path ใช้กับที่มีช่องว่างไม่ได้ตรง ๆ** — `docs/install/macos.md:28` และ `docs/install/linux.md:28`
    
    ```sh
    python3 bootstrap_windows.py --exchange /absolute/path/to/exchange \
      --local-dir /absolute/path/to/installation --node windows
    ```
    
    คู่มือ macOS เองใช้ `Application Support` เป็นค่าเริ่มต้น แต่ตัวอย่างนี้ไม่ครอบ path ด้วยเครื่องหมายคำพูด ผมต้องเติมเอง และแม้จะอธิบายว่าเป็นตัวติดตั้งข้ามระบบ ชื่อ `bootstrap_windows.py` ก็ยังทำให้ต้องหยุดเช็กอีกรอบว่าคัดลอกคำสั่งผิดหน้าหรือเปล่า

13. **พาไปรัน foreground แล้วไม่บอกว่าตรวจต่อในอีก terminal** — `docs/install/macos.md:47` และ `docs/install/linux.md:47`
    
    > The terminal stays occupied until you press Ctrl-C.
    
    ต่อท้ายให้ไปตรวจ status แต่ไม่บอกเปิดอีก terminal คนรีบ ๆ อาจกด Ctrl-C เพื่อพิมพ์คำสั่งถัดไปแล้วปิด worker ที่กำลังจะตรวจเสียเอง เป็นช่องว่างเล็กที่เติมหนึ่งประโยคก็หาย

14. **ให้ผมตรวจ platform ที่ผู้เขียนยังไม่ได้ตรวจ แต่ไม่มีเกณฑ์ครบ** — `docs/install/linux.md:64`
    
    > Wrapper tests on macOS do not validate Linux systemd behavior; verify operation on your Linux host before relying on it.
    
    และ `docs/install/windows.md:73`
    
    > Verify these on the Windows host.
    
    ชอบที่บอกข้อจำกัดตรง ๆ แต่ผมต้องตรวจอะไรบ้างจึงถือว่าใช้ได้? ต้องผ่าน reboot, login, firewall, import ข้ามเครื่องอย่างไร? “ตรวจเอง” ยังไม่ใช่ขั้นตอนทดสอบ

15. **ให้ inspect service configuration แต่ไม่บอกให้มองหาอะไร** — `docs/install/background-service.md:13`
    
    > Inspect the generated file before running the registration commands; stop an old service before explicitly removing its configuration to regenerate it.
    
    ผมควรตรวจ path, interpreter, database หรืออะไรอีก? `plutil -lint` ตรวจรูปแบบผ่านแล้วไม่ได้แปลว่าชี้ฐานข้อมูลถูก ขอรายการจุดตรวจและตัวอย่างสถานะหลังเริ่ม service ที่ถือว่าปกติด้วย

16. **รันเบื้องหลังบน Linux อาจหยุดเมื่อ logout แล้วให้ผมไปหาทางเอง** — `docs/install/background-service.md:74`
    
    > This requires a working systemd user session and may stop on logout.
    
    ถ้าผมใช้เครื่อง Linux ผ่าน SSH ความต่างนี้สำคัญมาก ควรมีทางเลือกชัดว่าอยากให้รันเฉพาะตอน login หรือหลัง logout ด้วย และลิงก์ขั้นตอนสำหรับแต่ละแบบ

17. **Windows ติดตั้งแล้ว แต่จะ autostart ต้องติดตั้งอีกประเภทหนึ่ง** — `docs/install/background-service.md:79`
    
    > Bootstrap alone does not create the owned program state required by autostart.
    
    อ้าว แล้วที่ทำมาเรียกติดตั้งแบบไหน? หน้านี้ส่งไปเอกสาร operator แต่ไม่ได้อธิบายสั้น ๆ ว่าต้องหา bundle จากไหน ย้ายจาก bootstrap ไปแบบนั้นอย่างไร และใช้ฐานข้อมูลเดิมหรือไม่ นี่เป็นทางแยกใหญ่เกินกว่าจะซ่อนไว้หลังคำว่า background

18. **เตรียม Python ตามที่บอกแล้ว กลับต้องเปลี่ยนรุ่น** — `docs/install/new-group.md:11`
    
    > Use Python 3.11 or later for this command.
    
    หน้า prerequisites บอก 3.10 ขึ้นไป แต่เส้นทางที่ตรงกับคนเริ่มจากศูนย์ต้องใช้ 3.11 ความต่างนี้ควรอยู่ก่อนติดตั้ง Python รวมทั้งภาพ `docs/public/diagrams/install-flow.svg:24` ที่ยังเขียน “Python 3.10+ with FTS5”

19. **กลุ่มแรกต้องใช้ folder ที่ accepted แล้ว ใครเป็นคนเสนอให้ accept?** — `docs/install/new-group.md:13`
    
    > Use an accepted private Syncthing exchange containing its `.stfolder` directory.
    
    สำหรับเครื่องแรกของกลุ่มใหม่ ผมเป็นคนสร้าง folder เองหรือรับจากเครื่องที่สอง? `.stfolder` เกิดจาก Syncthing หรือผมต้องสร้าง? และข้อห้าม symlink ใช้กับ path ทั้งสายอย่างไร? อย่าให้มือใหม่ต้องตีความกติกา path ก่อนสร้างกลุ่มแรกเลย

20. **สร้างกลุ่มแล้ว แต่ยังไม่ได้ติดตั้งแอป — แล้วไปทางไหนต่อ?** — `docs/install/new-group.md:6`
    
    > This command creates local state only; it does not copy application code or join an existing group.
    
    บรรทัด 19 ให้เริ่มจาก repository root โดยไม่มีขั้น clone หรือระบุรุ่นที่จะใช้ จากนั้นบรรทัด 40 ให้ใช้ `memory-sync` ใน checkout ทำ ingestion ได้ แต่ยังไม่บอกให้ใช้ checkout นั้นเป็นแอปถาวร หรือคัดลอกไปที่ไหน ก่อนหน้านี้ service instructions กลับสมมติว่ามี `$LOCAL/app` แล้ว

21. **เครื่องที่สองไปต่อไม่ได้ด้วยคู่มือนี้** — `docs/install/new-group.md:38`
    
    > To add another device to this same group, arrange a private bootstrap with the operator; creating another empty group is not a join procedure.
    
    **ผมนี่แหละคนเริ่มกลุ่ม ไม่มี operator อีกคน** ต้องสร้าง bootstrap อย่างไร เลือก identity ปลายทางที่ไหน ส่งไฟล์อะไร แล้วเครื่องที่สองเริ่มจากข้อไหน? ลิงก์ข้างประโยคนี้พาไป implementation ไม่ใช่ขั้นตอนสำหรับคนใช้ นี่คือจุดที่เป้าหมาย “เครื่องของผมหลายเครื่อง” หยุดจริง ๆ

22. **เจอ setup ค้างแล้วไม่มีทางตามต่อเฉพาะอาการ** — `docs/install/new-group.md:29`
    
    > A retained `.setup-pending.json` indicates incomplete setup requiring review; do not bypass it by copying state or rerunning into another identity.
    
    เข้าใจว่าห้ามฝืน แต่ review อย่างไร? มีคำสั่งดูเหตุ มีตัวอย่างอาการ หรือมีขั้นตอนกู้คืนไหนให้เปิด? ไม่อยากต้องเดาว่าการลองครั้งต่อไปจะทำให้แย่ลงไหม

23. **Signed sync ไม่มีคำตอบแรกสุดว่าผมควรทำคืนนี้หรือข้ามได้** — `docs/install/security.md:3`
    
    > This page helps you inspect security readiness and resume setup; coordinated activation remains an operator procedure.
    
    ผมต้องตัดสินใจเองว่ากลุ่มส่วนตัวใช้แบบ unsigned ได้ตามที่คู่มือตั้งใจหรือไม่ แล้วคำสั่ง `wizard-resume` พาไป activation ได้ แต่การเตรียม dependencies, การยืนยันอีกเครื่อง และการเปลี่ยน service กระจายไปหลาย reference งานที่ดูเหมือน wizard ยังต้องมีคนรู้ระบบคุมอยู่

24. **อัปเดตแบบเริ่มจาก checkout ไม่มีทางเดินชัดเจน** — `docs/install/update-remove.md:3`
    
    > Start with your [installation details](../use/worker#inspect-the-installation-first) and identify whether you use bootstrap source files or a separately installed program bundle.
    
    แล้วผมที่ทำ New sync group และใช้ repository อยู่เข้าช่องไหน? `inspect-install` ควรแสดงค่าอะไรที่ใช้เลือกเส้นทาง? ไม่มีตัวอย่างผลและไม่มีขั้น update สำหรับเส้นทางนี้โดยตรง

25. **Windows bundle ถูกสมมติว่าหามาได้แล้วอีกครั้ง** — `docs/install/update-remove.md:19`
    
    > Use PowerShell in the new bundle extracted locally outside the exchange, with `agentmesh.exe`, adjacent `BUILD.json` and all bundled files intact.
    
    ดาวน์โหลดจากไหน เลือกรุ่นไหน และเกี่ยวกับ bootstrap ZIP อย่างไร? ก่อนหน้านี้ห้ามแตก bootstrap เอง ตรงนี้ให้แตก bundle จึงควรแสดงชื่อและความต่างของสองสิ่งให้เห็นชัด

26. **มีวิธีเปิด worker หลายแบบ แต่ไม่มีคำแนะนำให้เลือกแบบเดียวก่อน** — `docs/use/worker.md:3`
    
    > Run one sync cycle, keep a foreground loop open, or control a managed worker so memory continues moving between devices.
    
    ตอนนี้ผมเห็นทั้ง `sync_worker.py`, `once`, `watch`, `worker-run`, `worker-start` และ OS service แล้ว อะไรควรเป็นค่าเริ่มต้นสำหรับคนเพิ่งติดตั้ง? ตัวไหนรับผิดชอบ ingestion ตาม `workflow.json` และตัวไหนควบคุมด้วย `worker-stop` ได้? มีคำอธิบายกระจายอยู่ แต่ต้องประกอบแผนผังเอง

27. **ห้ามเปิด worker ซ้ำ แต่เครื่องมือตรวจเองก็ยืนยันไม่ได้** — `docs/use/worker.md:61`
    
    > Missing metadata can report stopped even when an unrelated worker exists.
    
    ถ้าผมจำไม่ได้ว่าเปิดจากหน้าต่างไหน จะหาและระบุ worker ของ installation นี้อย่างไร? บอกให้ตรวจว่าหยุดแล้วหลายครั้ง แต่ไม่มีขั้นตรวจ process ของแต่ละ OS สำหรับกรณีที่ตัวจัดการไม่รู้จัก

28. **กลุ่มใหม่ปิด ingestion แต่ไม่มีขั้นเปิดให้ทำงานต่อเนื่อง** — `docs/use/worker.md:31`
    
    > Ingestion and summarization depend on that configuration; an empty group starts with both disabled.
    
    ต้องแก้ field ไหนใน `workflow.json`? มีตัวอย่างขั้นต่ำไหม? ถ้าใช้ manual ingest ได้ก็บอกให้ชัดว่าต้องทำซ้ำเอง และไม่มี summaries ถือว่าปกติ ไม่ใช่ติดตั้งเสีย

29. **หน้า Recall กลับสมมติว่ามี installed app** — `docs/use/recall.md:13`
    
    > From the installed `app` directory, use your initialized database:
    
    คนมาจาก New sync group ยังไม่มี directory นี้ หน้า ingestion และ worker อนุญาตให้ใช้ `memory-sync` แต่หน้านี้ไม่บอก ผมต้องอนุมานเอง รวมถึงตอนติดตั้ง agent rules ว่าจะชี้ `--app-dir` ไป checkout ได้หรือควรติดตั้งก่อน

30. **ติดตั้ง rules แล้ว ยังไม่รู้ว่า agent ใช้จริงหรือยัง** — `docs/use/recall.md:36`
    
    > The installer writes instructions and an adjacent command manifest, and refuses unowned files rather than overwriting them.
    
    ถ้าเจอ unowned file ต้องทำอย่างไร? หลังสำเร็จต้องเปิด session ใหม่หรือไม่? ขอประโยคให้ลองถาม agent และตัวอย่างหลักฐานว่ามันเรียก AgentMesh จริง ไม่ใช่ตอบจากความจำในบทสนทนาเดิม

31. **สัญญาว่ารองรับ Hermes แต่ทาง ingest ไม่มี Hermes** — `docs/use/ingest.md:37`
    
    > Supported agents are `omp`, `codex` and `claude`.
    
    หน้า introduction ระบุ Hermes และหน้า recall ติดตั้ง rules ให้ Hermes ได้ แต่ตาราง ingestion ไม่มี Hermes ผมใช้ Hermes อย่างเดียวจะนำบทสนทนาเข้าอย่างไร หรือรองรับเฉพาะ recall? ต้องแยกความสามารถตั้งแต่หน้าต้น ๆ

32. **custom ingestion กับการจัด project ยังต้องเดา** — `docs/use/ingest.md:47`
    
    > Custom session locations need explicit configuration or a manual command.
    
    explicit configuration อยู่ไหนและเขียนอย่างไร? อีกทั้งตัวอย่าง `ingest --project AgentMesh` ที่บรรทัด 44 ดูเหมือนติด project เดียวให้ทุก session ที่ค้นพบ ถ้าผมมีหลาย repo ต้องจำกัดชุดนำเข้ายังไง และถ้านำเข้าไปแล้วการเติม `--project` รอบหลังมีผลหรือไม่?

33. **ตรวจ sync ได้หลายอย่าง แต่ยังไม่มีบทพิสูจน์ว่าข้ามเครื่องสำเร็จ** — `docs/use/status.md:17`
    
    > The JSON result contains memory-table counts and local sync state, including unpublished outbox, received, pending, conflict and invalid counts.
    
    ไม่มีตัวอย่าง JSON ที่ปกติหรือผิดปกติ ไม่มีเกณฑ์ว่า pending ค้างนานเท่าไรควรสงสัย และไม่มีการทดสอบ “สร้างข้อความเฉพาะบน A แล้ว recall บน B” ผมอาจเห็นคำสั่งสำเร็จทุกอันแต่ยังไม่รู้ว่าความทรงจำเดินทางจริงไหม

34. **Insight ต้องการ profile ที่ผมไม่เคยสร้าง** — `docs/use/insight.md:28`
    
    > Use an existing SQLite database and the runtime profile directory containing your metrics and job files:
    
    นี่คือ directory `data` ของ AgentMesh หรือ profile ของ Hermes? ผมใช้ Codex อย่างเดียวต้องใส่อะไร? บรรทัด 35 บอกค่าเริ่มต้น `~/.hermes` แต่ไม่ได้บอกว่าคนไม่มี Hermes ควรใช้คำสั่งไหน และ view ไหนว่างได้ตามปกติ

35. **Troubleshooting อธิบายว่าอย่าทำอะไร มากกว่าบอกว่าจะออกจากปัญหาอย่างไร** — `docs/troubleshooting.md:17`
    
    > Keep backups before operator-led reconciliation and follow [Safety and conflicts](https://github.com/theidentify/AgentMesh/blob/main/memory-sync/SYNC.md#safety-and-conflicts).
    
    ผมเป็นผู้ดูแลเครื่องตัวเอง ต้องสำรองไฟล์ไหน หยุดอะไร และใช้วิธีไหนให้ snapshot สอดคล้องกัน? เมื่อ checksum ยังผิดก็บอกให้ถาม “trusted package provider” ที่บรรทัด 23 เมื่อ copy database ผิดก็บอกให้เก็บไว้ “for review” ที่บรรทัด 31 คนอ่านจาก GitHub ยังไม่มีคนเหล่านี้

36. **ปกป้องข้อมูลถูกต้อง แต่ยังไม่รู้จะขอความช่วยเหลืออย่างไร** — `docs/privacy.md:27`
    
    > Do not treat raw status JSON as guaranteed safe to publish.
    
    แล้ว issue ที่ดีควรแนบอะไร? มี `diagnose --json` ที่บอกว่า redacted แต่ไม่มีแม่แบบให้ตรวจและส่งเฉพาะ field ที่จำเป็น ผมไม่อยากเลือกระหว่างเผยข้อมูลส่วนตัวกับส่งรายงานที่ไม่มีประโยชน์

37. **อ่านจนจบแล้วถูกส่งกลับทางที่ยังไม่มีของตั้งต้น** — `docs/reference/index.md:34`
    
    > Next, return to [Quick start](../getting-started/quick-start) for the end-user setup path.
    
    Quick start ยังต้องมี bootstrap เหมือนเดิมครับ ส่วน workflow, signed sync และการติดตั้ง Windows ที่ต้องใช้จริงถูกจัดไว้ฝั่ง Operators ป้ายหมวดไม่ได้ช่วยให้คนตั้งเครื่องเองข้ามช่องว่างนี้ได้

**2. คำที่ถูกใช้ก่อนอธิบาย หรืออธิบายไม่พอให้ลงมือทำ**

คำพื้นฐานอย่าง Git, PATH และ CLI ไม่ใช่ปัญหาสำหรับผม แต่คำเฉพาะเหล่านี้ทำให้ต้องหยุดอ่าน:

| ตำแหน่งแรกที่สะดุด | ข้อความตรงจากคู่มือ | สิ่งที่ผมยังไม่รู้ในจังหวะนั้น |
|---|---|---|
| `docs/introduction.md:7` | “private Syncthing folder” | Syncthing ทำหน้าที่อะไร และความเป็น private ต้องตั้งค่าเองอย่างไร |
| `docs/introduction.md:7` | “change packets” | อธิบายภายหลังที่ Concepts แต่ตอนแรกยังไม่รู้ว่าต่างจากไฟล์ฐานข้อมูลอย่างไร |
| `docs/introduction.md:11` | “Hermes, OMP, Codex or Claude” | โดยเฉพาะ Hermes กับ OMP คือแอปไหน ไม่มีลิงก์ระบุตัว |
| `docs/introduction.md:11` | “recall rules” | เป็น plugin, config หรือข้อความให้ agent อ่าน? กว่าจะเห็นวิธีจริงอยู่หน้า Recall |
| `docs/introduction.md:11` | “private bootstrap” | นิยาม ZIP มาทีหลัง แต่ที่มาและผู้สร้างยังไม่ชัด |
| `docs/introduction.md:11` | “empty group” | group รวมอะไรบ้าง และสัมพันธ์กับ Syncthing folder อย่างไร |
| `docs/introduction.md:15` | “setup wrappers” | หมายถึงไฟล์ใดบ้าง ไม่ควรต้องรอหน้าติดตั้งจึงจับคู่ชื่อได้ |
| `docs/getting-started/quick-start.md:7` | “accept the private exchange” | หมายถึงการกดรับ folder ใน Syncthing ไม่ใช่คำสั่ง AgentMesh ใช่ไหม |
| `docs/getting-started/quick-start.md:9` | “foreground worker” | Concepts อธิบายภายหลัง แต่ Quick start ใช้ก่อน |
| `docs/getting-started/concepts.md:13` | “WAL/SHM companions” | ไฟล์ประกอบอะไร เกิดที่ไหน และต้องดูแลพร้อมฐานข้อมูลอย่างไร |
| `docs/getting-started/concepts.md:19` | “sync group UUID” | ใครสร้าง เก็บที่ไหน ดูเพื่อเทียบสองเครื่องอย่างไร |
| `docs/getting-started/concepts.md:23` | `status/<node>.json` | `<node>` คือ identity `mac`/`windows`/`linux` หรือชื่อเครื่องของ Syncthing |
| `docs/getting-started/concepts.md:31` | “natural unique key” / “quarantined” | ไม่รู้ SQL ภายในก็ใช้โปรแกรมได้สิ ควรอธิบายผลต่อข้อมูลและสิ่งที่ผู้ใช้จะเห็น |
| `docs/getting-started/concepts.md:39` | “PostgreSQL” | เป็น dependency หรือทางเลือก? ไม่มีบริบทก่อนหน้า |
| `docs/getting-started/prerequisites.md:11` | “SQLite FTS5 and JSON support” | มีวิธีตรวจ แต่ไม่มีคำอธิบายว่าความสามารถไหนใช้ทำอะไรและแก้อย่างไรเมื่อขาด |
| `docs/install/macos.md:61` | “configured managed worker” | ยังไม่ได้อธิบายความต่างจาก worker ที่เพิ่งเปิดไว้ |
| `docs/install/background-service.md:74` | “lingering” | ศัพท์ systemd ที่โยนมาโดยไม่แปลเป็นพฤติกรรมหลัง logout |
| `docs/install/background-service.md:79` | “owned program state” | ใคร owns อะไร และเกิดจากการติดตั้งแบบไหน |
| `docs/install/background-service.md:93` | “signed policy” / “managed-worker metadata” / “source workers” | ใช้เป็นเงื่อนไขเลือก flag ก่อนมีภาพรวมประเภท worker และ policy |
| `docs/install/new-group.md:29` | “workflow” / “summarization” | รูปแบบงานและการสร้างสรุปยังไม่มีตัวอย่างหรือขั้นเปิดใช้ |
| `docs/install/new-group.md:38` | “the operator” | เป็นบทบาทที่ผมต้องรับเอง คนของโปรเจกต์ หรือผู้ส่ง bootstrap? |
| `docs/install/security.md:11` | “Ed25519” / “unsigned telemetry” | ควรแปลเป็นผลต่อผู้ใช้ก่อนลงชื่อเทคนิค |
| `docs/install/security.md:13` | “Peer proposals” / “fingerprint and scope” | ข้อเสนอหน้าตาอย่างไร เอา fingerprint จากไหน และ scope หมายถึงอะไร |
| `docs/install/security.md:31` | “isolated signed probe” | ทดสอบอะไร ผ่านแล้วรับประกันอะไรบ้าง |
| `docs/install/security.md:34` | “unsigned history drained” / “probe receipt” / “production memory convergence” | เป็นเงื่อนไขสำคัญ แต่ไม่มีวิธีตรวจในภาษาคนติดตั้ง |
| `docs/install/update-remove.md:31` | “owned login-task binding” | ยังไม่รู้ว่าผูก task กับ executable หรือ installation ใด |
| `docs/use/worker.md:59` | “lifetime lock” | ไม่รู้ว่าต้องทำอะไรเมื่อรอแล้วไม่ drain |
| `docs/use/recall.md:7` | “memory items, summaries, observation fallbacks and evidence IDs” | ผลลัพธ์แต่ละชนิดต่างกันอย่างไร และเปิดหลักฐานต้นทางจาก ID อย่างไร |
| `docs/public/diagrams/recall-flow.svg:27` | “runs recall_argv” | identifier โผล่ในภาพ แต่ไม่มีคำอธิบายในหน้านั้น |
| `docs/use/recall.md:36` | “adjacent command manifest” / “unowned files” | ไฟล์ไหนอยู่ข้างไฟล์ไหน และกรณีถูกปฏิเสธควรทำอะไร |
| `docs/use/ingest.md:13` | “JSONL” | รูปแบบ transcript ที่ต้องมี หน้าตาและข้อกำหนดขั้นต่ำไม่แสดง |
| `docs/use/ingest.md:24` | “PostgreSQL mirror state” | รู้ได้อย่างไรว่าฐานข้อมูลผมมี state นี้ |
| `docs/use/ingest.md:57` | “psycopg” | ชื่อ dependency ที่โผล่มาเพื่อบอกว่าไม่ต้องใช้ ยิ่งทำให้สงสัยว่าพลาด prerequisite หรือเปล่า |
| `docs/use/status.md:17` | “unpublished outbox, received, pending, conflict and invalid counts” | ไม่มีอภิธานศัพท์ของสถานะพร้อมตัวอย่างและเกณฑ์ดำเนินการ |
| `docs/use/insight.md:28` | “runtime profile directory” | ยังแยกไม่ออกจาก installation directory และ directory ที่มี `runtime.json` |
| `docs/use/insight.md:35` | “Authority labels” | หมายถึงความน่าเชื่อถือของข้อมูล หรือแหล่งหลักที่เขียนข้อมูลได้ |
| `docs/use/insight.md:38` | “DSN” | มีลิงก์รูปแบบ แต่ไม่อธิบายสั้น ๆ ว่าเป็นข้อมูลเชื่อมต่อฐานข้อมูล |
| `docs/reference/index.md:20` | “OTA updates” | เป็นช่องทางอัปเดตอีกแบบที่เพิ่งโผล่ หลังอ่านวิธีอัปเดตไปแล้ว |

**3. ขั้นตอนที่ขาดหรือสมมติว่าผมมีอยู่แล้ว**

ช่องว่างที่ต้องเติมเพื่อให้ผมทำสำเร็จ ไม่ใช่แค่ปรับถ้อยคำ มีดังนี้:

- **เริ่มจาก GitHub จนรันคำสั่งแรกได้:** clone, เลือกรุ่น, เข้า directory และเลือก Python ให้ตรงเส้นทาง — `docs/install/new-group.md:11`, `docs/install/new-group.md:19`
- **ตั้ง Syncthing ตั้งแต่ศูนย์:** ติดตั้ง เปิด UI เพิ่มอีกเครื่อง สร้างและแชร์ folder รับ folder และยืนยัน path ของแต่ละเครื่อง — `docs/getting-started/prerequisites.md:27`, `docs/getting-started/prerequisites.md:31`
- **เครื่องแรกสร้าง bootstrap ให้เครื่องที่สอง:** คำสั่ง ข้อมูลที่ต้องเตรียม การเลือก identity และจุดเริ่มของเครื่องรับ — `docs/install/new-group.md:38`
- **ทำให้กลุ่มใหม่ใช้งานต่อเนื่อง:** เลือกตำแหน่งแอป เปิด ingestion ตั้ง custom roots เริ่ม worker และตั้งเริ่มหลัง login — `docs/install/new-group.md:29`, `docs/use/worker.md:31`, `docs/use/ingest.md:47`
- **Windows จาก bootstrap ไป autostart:** ที่มาของ program bundle และขั้นเปลี่ยนรูปแบบติดตั้งโดยใช้ข้อมูลเดิม — `docs/install/background-service.md:79`
- **ยืนยันผลตั้งแต่ต้นจนจบ:** นำข้อความที่รู้จักเข้าบน A แล้วค้นพบพร้อมหลักฐานบน B จากนั้นให้ agent เรียกได้จริง — `docs/getting-started/quick-start.md:15`, `docs/getting-started/quick-start.md:16`, `docs/getting-started/quick-start.md:17`
- **ดูแลหลังติดตั้ง:** หา worker ที่เปิดค้าง สำรองอย่างถูกวิธี แก้ setup ค้าง และเตรียมข้อมูลขอความช่วยเหลือ — `docs/use/worker.md:61`, `docs/install/new-group.md:29`, `docs/troubleshooting.md:17`, `docs/privacy.md:27`
- **ถอนการเชื่อม agent เมื่อเลิกใช้:** ขั้น remove กล่าวถึงแอปและ service แต่ไม่ได้พาปลด rules ที่เคยติดตั้งไว้ ซึ่งมีคำสั่งชี้ไปยังแอปที่อาจถูกลบแล้ว — `docs/install/update-remove.md:52`, `docs/use/recall.md:36`

**4. อะไรทำให้ผมอยากเลิก และเพราะอะไร**

- **หนักสุด: “arrange a private bootstrap with the operator”** — `docs/install/new-group.md:38`  
  ผมอยากใช้บนเครื่องของตัวเอง แต่เส้นทางหลักพาไปพึ่งคนที่ไม่มีอยู่ในสถานการณ์ของผม ต่อให้ทุกคำสั่งก่อนหน้านั้นผ่าน ผมก็ยังไม่ถึงเป้าหมาย

- **“Pair only trusted devices and accept the private exchange folder.”** — `docs/getting-started/prerequisites.md:31`  
  นี่ไม่ใช่ขั้นตอนสำหรับคนไม่เคยใช้ Syncthing เป็นสรุปสำหรับคนที่ทำเสร็จแล้ว ผมต้องออกไปเรียนอีกระบบก่อนกลับมาต่อ โดยคู่มือไม่ได้ช่วยชี้ทาง

- **“Bootstrap alone does not create the owned program state required by autostart.”** — `docs/install/background-service.md:79`  
  ทำตามจนติดตั้งเสร็จแล้วเพิ่งรู้ว่าการใช้งานประจำวันที่ต้องการต้องผ่านอีกระบบติดตั้ง ความรู้สึกคือทำงานไปแล้วแต่ยังไม่ได้เริ่มส่วนที่ต้องใช้จริง

- **“Linux-host verification remains outstanding.”** — `docs/install/linux.md:64` และข้อจำกัด Windows ที่ `docs/install/windows.md:73`  
  ไม่ได้รำคาญความซื่อสัตย์ แต่รำคาญที่ไม่มีชุดตรวจให้ทำตาม ผมต้องกลายเป็นคนออกแบบการทดสอบเอง ทั้งที่ยังไม่รู้ว่าหน้าตาของระบบที่ถูกต้องเป็นอย่างไร

- **คำเตือนมีละเอียด แต่ทางออกสั้นมาก** — เช่น `.setup-pending.json` ที่ `docs/install/new-group.md:29` และ conflict ที่ `docs/troubleshooting.md:17`  
  ผมถูกบอกว่าห้าม copy ห้ามลบ ห้าม retry แบบเดา ซึ่งสมเหตุผล แต่พอถามว่า “แล้วทำอะไรได้?” คำตอบกลับเป็น review กับ operator อีกแล้ว

**5. สามการเปลี่ยนแปลงที่จะช่วยผมมากที่สุด**

1. **ทำเส้นทาง “เริ่มจาก GitHub ใช้สองเครื่องของตัวเอง” ให้ครบในหน้าเดียว**  
   เริ่มจากไม่มี bootstrap จนสร้างกลุ่มบน A ส่ง bootstrap ไป B เลือก identity ไม่ซ้ำ เปิด worker ทั้งคู่ และ recall ข้อความเดียวกันได้ ต้องรวมขั้น Syncthing จริง ๆ ด้วย ส่วนเส้นทางผู้ได้รับ bootstrap ให้เป็นอีกทางเลือกที่ชัดเจนตั้งแต่หน้าแรก

2. **เลือกวิธีติดตั้งและเปิด worker ที่แนะนำหนึ่งแบบต่อสถานการณ์ แล้วใช้ path ชุดเดิมตลอดคู่มือ**  
   แสดงให้เห็นว่าคนสร้างกลุ่มใหม่ คนรับ bootstrap และคนใช้ bundle ต่างกันตรงไหน พร้อมคำสั่งครบสำหรับ foreground, background, restart และ update อย่าให้ผมประกอบความสัมพันธ์ระหว่าง `app`, checkout, runtime, workflow และ managed worker เอง และระบุ Python 3.11 ตั้งแต่แรกสำหรับเส้นทางสร้างกลุ่ม

3. **เพิ่มการทดสอบความสำเร็จและทางแก้ที่ใช้ได้โดยไม่ต้องมี operator คนอื่น**  
   ให้ตัวอย่างผล install/status/recall ที่ควรเห็น การทดสอบ A → B การตรวจว่า agent เรียก recall จริง และขั้นกู้คืนอาการพบบ่อย พร้อมแม่แบบขอความช่วยเหลือที่ตรวจข้อมูลส่วนตัวแล้วได้ ผมอยากได้คำตอบว่า “ตอนนี้ใช้ได้แล้ว” ที่พิสูจน์ได้ ไม่ใช่แค่ไม่มีคำสั่งไหนฟ้อง error