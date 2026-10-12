**สถานะข้อเดิมก่อน — รอบนี้แก้ไปเยอะ แต่ยังไม่หมด**

เลขด้านล่างอ้างถึงข้อ 1–37 ในรายงานรอบก่อน ประเมินจากคู่มือปัจจุบัน ไม่ได้รันติดตั้งจริง

| สถานะ | ข้อเดิม | หลักฐานและสิ่งที่ยังเหลือ |
|---|---|---|
| แก้แล้ว | 2, 37 | แยกคนรับ bootstrap กับคนเริ่มเอง และส่งกลับถูกเส้นทางแล้ว — `docs/getting-started/quick-start.md:7`, `docs/reference/index.md:34` |
| แก้แล้ว | 3, 5 | ให้ตรวจรายการเครื่องกับผู้ดูแล รวมเครื่อง offline; อธิบายสอง Mac และข้อจำกัดเครื่องที่สี่แล้ว — `docs/getting-started/prerequisites.md:43`, `docs/getting-started/concepts.md:21` |
| แก้แล้ว | 4, 6, 31 | อธิบายเงื่อนไข ingestion, PostgreSQL ที่ไม่จำเป็น และ Hermes ที่รองรับเฉพาะ recall — `docs/getting-started/concepts.md:11`, `docs/getting-started/concepts.md:37` |
| แก้แล้ว | 8, 9, 10, 11, 19 | มีทางติดตั้ง Syncthing, GUI, pairing, `.stfolder`, รายชื่อไฟล์ และ directory เริ่มต้นแล้ว — `docs/getting-started/prerequisites.md:39`, `docs/getting-started/prerequisites.md:45`, `docs/getting-started/prerequisites.md:55`, `docs/install/macos.md:15`, `docs/install/linux.md:15` |
| แก้แล้ว | 12, 13 | ครอบ path และบอกให้เปิด terminal ที่สองแล้ว — `docs/install/macos.md:30`, `docs/install/macos.md:51`; Linux แก้เช่นกัน |
| แก้แล้ว | 14, 15, 16 | มีรายการตรวจเครื่องจริง จุดตรวจ service และขอบเขตการทำงานหลัง logout พร้อมลิงก์แล้ว — `docs/install/linux.md:70`, `docs/install/windows.md:81`, `docs/install/background-service.md:13`, `docs/install/background-service.md:86` |
| แก้แล้ว | 18, 23 | ระบุ Python ตามเส้นทางตั้งแต่ prerequisites และบอกว่าการทดสอบแรกเลื่อน signed sync ได้ — `docs/getting-started/prerequisites.md:11`, `docs/public/diagrams/install-flow.svg:24`, `docs/install/security.md:9` |
| แก้แล้ว | 26, 28, 29, 32 | มีตารางเลือก worker, ตัวอย่าง workflow, path สำหรับ checkout และความหมายของ `--root`/`--project` — `docs/use/worker.md:7`, `docs/use/ingest.md:44`, `docs/use/ingest.md:60`, `docs/use/recall.md:66` |
| แก้แล้ว | 33, 34, 36 | มี A → B test, อธิบาย profile ของ Insight และรายการข้อมูลที่ส่งขอความช่วยเหลือได้ — `docs/getting-started/quick-start.md:47`, `docs/use/insight.md:28`, `docs/troubleshooting.md:71` |
| แก้บางส่วน | 1 | ท้ายหน้าแยกเส้นทางชัดแล้ว แต่หัวข้ออ่านตามลำดับยังเริ่ม Concepts และพาทุกคนไปหน้าติดตั้งตาม OS — `docs/introduction.md:25`, `docs/introduction.md:31` |
| แก้บางส่วน | 7 | มีลิงก์ติดตั้งและผลตรวจแล้ว แต่เมื่อ SQLite ขาดความสามารถ ยังบอกเพียงให้หา Python build ที่รองรับ — `docs/getting-started/prerequisites.md:33` |
| แก้บางส่วน | 20, 24 | บอกแล้วว่าใช้ checkout เป็นแอปถาวรและอัปเดตเส้นทางนี้อย่างไร แต่ยังไม่มีเกณฑ์เลือกรุ่นที่จะติดตั้งหรืออัปเดตไป — `docs/install/new-group.md:11`, `docs/install/update-remove.md:23` |
| แก้บางส่วน | 21 | แก้ความเข้าใจผิดในเอกสารแล้ว: บอกชัดว่าเพิ่มเครื่องยังไม่ได้ แต่เป้าหมายใช้หลายเครื่องจากกลุ่มที่สร้างเองยังทำไม่สำเร็จ — `docs/install/new-group.md:53` ไม่ยกเป็นปัญหาใหม่ |
| แก้บางส่วน | 22, 27, 35 | มีวิธีเก็บหลักฐาน ตรวจ diagnostics และขอความช่วยเหลือ แต่การกู้ setup, ระบุ worker ที่หาไม่เจอ และทำ backup ยังต้องพึ่งความรู้เพิ่มเติม — `docs/install/new-group.md:33`, `docs/use/worker.md:119`, `docs/troubleshooting.md:35` |
| แก้บางส่วน | 30 | มีการทดสอบว่า agent เรียกจริงแล้ว แต่เมื่อ rule ไม่ถูกโหลดหรือชนไฟล์เดิม ยังต้องไปหาขั้นตอนต่อเอง — `docs/use/recall.md:56`, `docs/use/recall.md:72` |
| ไม่นำมาทวงซ้ำตาม triage | 17, 25 | เรื่องแหล่ง Windows program bundle และขั้นตอน operator อยู่นอกขอบเขตรอบนี้ |

ไม่มีข้อที่มีหมายเลขซึ่งผมจัดว่า “ยังเปิดอยู่โดยไม่มีความคืบหน้าเลย” ส่วนบัญชีศัพท์เดิมไม่มีหมายเลข จึงไม่นำมาตั้งเลขย้อนหลังหรือรายงานซ้ำเป็นข้อใหม่

อ่านครบตาม sidebar ตั้งแต่ `docs/introduction.md` ถึง `docs/reference/index.md` รวมข้อความในภาพประกอบ และตามลิงก์เอกสารอ้างอิงบางจุด ไม่ได้อ่านซอร์สโค้ด รันติดตั้ง หรือแก้ไฟล์

**ปัญหาใหม่ — จุดที่ผมยังต้องเดาและอยากปิด terminal**

**ใหม่ 1 — ให้เลือกข้อมูลที่จะนำเข้าหลังตัวติดตั้งเริ่มนำเข้าไปแล้ว**

`docs/getting-started/concepts.md:15`:

> A bootstrap enables ingestion; an empty group starts with ingestion disabled.

`docs/use/ingest.md:66`:

> Choose non-overlapping sources before enabling discovery on several devices.

แต่หน้าติดตั้งเริ่ม worker ทันที เช่น `docs/install/windows.md:29`:

> `Ready: local database installed; starting synchronization`

อ้าว แล้วผมเลือกก่อนมันเริ่มตรงไหน? รายชื่อ default roots อยู่ถึง `docs/use/ingest.md:15` และวิธีแก้ `workflow.json` อยู่บรรทัด 60 ซึ่งกำชับให้หยุด worker ก่อนอีกที

นี่เป็นปัญหาลำดับขั้นตอนใหม่: คู่มือบอกแล้วว่า ingestion เปิด แต่ยังไม่มีจุดให้ตรวจและจำกัดแหล่งข้อมูล **ก่อนรอบแรก** โดยเฉพาะเครื่องที่มี transcript ส่วนตัวปนงานหลายโครงการ หรือมีสำเนา transcript จากเครื่องอื่น ผมอยากทดสอบด้วยข้อความปลอมก่อน ไม่ใช่มารู้ภายหลังว่าควรเลือกแหล่งข้อมูลเสียก่อน

ควรมีขั้นตรวจ roots ก่อนคำสั่งติดตั้ง และบอกวิธีที่รองรับสำหรับเตรียม configuration ก่อนเริ่ม ingestion จริง

**ใหม่ 2 — บอกคน Windows ให้เปลี่ยนชื่อ Python แต่ยังทิ้งไวยากรณ์ shell ให้เดาเอง**

`docs/getting-started/quick-start.md:18`:

> Use `py -3` on Windows (expected from code, not run there).

แต่ A → B test ที่ `docs/getting-started/quick-start.md:60` ให้:

```sh
python3 agentmesh.py --database "/path/to/A-memory.db" ingest \
  --root "omp=/absolute/path/to/test-transcripts" --project GuideCheck
```

หน้า Windows เพิ่งพาผมใช้ Command Prompt มา เปลี่ยนแค่ `python3` เป็น `py -3` ก็ยังเหลือ `\` สำหรับต่อบรรทัด แล้วผมต้องเปลี่ยน terminal ด้วยไหม? ตัวอย่างติดตั้ง rules ที่ `docs/use/recall.md:45` และสร้างกลุ่มที่ `docs/install/new-group.md:20` มีช่องว่างแบบเดียวกัน

ผมรู้ command line แต่ไม่ได้หมายความว่าคำสั่งหลาย shell จะใช้แทนกันได้ ขอระบุ shell และให้ตัวอย่าง Windows แบบบรรทัดเดียวหรือไวยากรณ์ที่ตรงกัน โดยเฉพาะการทดสอบหลักที่บอกให้ทุกแพลตฟอร์มทำ

**ใหม่ 3 — ติดตั้ง Windows ไว้ที่อื่นได้ แต่คำแนะนำอัปเดตพากลับไป launcher ค่าเริ่มต้น**

`docs/install/windows.md:44`:

```bat
py -3 bootstrap_windows.py --exchange "C:\path\to\exchange" --local-dir "C:\path\to\installation" --node mac
```

จากนั้น `docs/install/windows.md:78` บอก:

> For this bootstrap-based setup, wait for Syncthing to finish, stop the current worker using its foreground, managed or service controls, and run **START-WINDOWS.cmd** again.

หน้า `docs/install/update-remove.md:11` เตือนให้เลือก identity เดิม แต่เงื่อนไขกรณีใช้ `--local-dir` ต่างจากค่าเริ่มต้นบน Windows ยังไม่ชัด โดยเฉพาะคนที่เปลี่ยนแค่ directory แต่ยังใช้ `--node windows`

ผมต้องรันคำสั่งเดิมทั้งชุด หรือ launcher จำ directory ของผมได้? จากคู่มือยังยืนยันไม่ได้ อย่าให้ผมต้องลองแล้วค่อยดูว่ามันไปติดตั้งตรงไหน ขอข้อยกเว้นชัด ๆ สำหรับทั้ง custom directory และ custom identity พร้อมคำสั่งอัปเดตที่รักษาค่าทั้งสอง

**ใหม่ 4 — Windows แบบ bootstrap ไม่มีเส้นทางถอนแอปของตัวเอง**

`docs/install/update-remove.md:27`:

> These commands require an existing program installation tracked and managed by the Windows installer, beyond bootstrap alone.

แล้วหัวข้อถอด source installation ที่ `docs/install/update-remove.md:62` กลับชื่อ:

> Remove a macOS or Linux source installation

ครับ ผมเป็น Windows ที่เพิ่งติดตั้งตามเส้นทางหลัก ใช้ `windows-uninstall` ก็ไม่เข้าเงื่อนไข ส่วนวิธีลบแอปเองก็ระบุอีกสอง OS เท่านั้น จะให้ผมเดาว่าลบ `%LOCALAPPDATA%\AgentMesh\app` ได้หรือเปล่า?

ข้อนี้ไม่ได้ขอแหล่งดาวน์โหลด bundle ซึ่ง triage ตัดออก ผมขอวิธีเลิกใช้ **bootstrap installation ที่คู่มือสอนให้ติดตั้งเอง** พร้อมรายการที่ต้องเก็บและขั้นถอด rules

**ใหม่ 5 — วิธีตั้ง ingestion ที่เพิ่งเพิ่ม ส่งผมไปหา reference ซึ่งบอกเรื่อง identity คนละอย่าง**

`docs/getting-started/concepts.md:21`:

> Two Macs and one Linux device can use the three different identities; a fourth device cannot join the same group under this model.

แต่ลิงก์ “Workflow reference” ที่ `docs/use/ingest.md:66` พาไป `memory-sync/WORKFLOW.md:15`:

> Use one logical node per OS: `mac`, `windows`, `linux`.

และบรรทัด 16:

> Mac is the single primary LLM summary writer.

ตกลง Mac หมายถึงเครื่อง macOS หรือ identity `mac`? ถ้าผมให้ Linux ใช้ `--node mac` ตาม Concepts ใครเป็นคนทำ summaries? ผมไม่ได้เปิดไฟล์สุ่มนะ ลิงก์ที่คู่มือบอกให้ตามพามาชนกันเอง

ควรทำศัพท์ใน reference ให้ตรงกับคู่มือ หรือระบุชัดว่าข้อความใดอธิบาย deployment เฉพาะ ไม่ใช่กติกาสำหรับผู้ใช้ทุกคน

**ใหม่ 6 — เพิ่มคำเตือนเรื่อง ingestion error แล้ว แต่ไม่มีวิธีเปิดดูสิ่งที่สั่งให้ตรวจ**

`docs/use/ingest.md:76`:

> Check `ingestion_errors` and recall the intended event; do not infer complete success from its exit code alone.

ส่วน `docs/use/ingest.md:56`:

> Preserve the original transcript, error record and cursor before recovery.

ผมจะเปิด error record กับ cursor อย่างไร? หน้า status แสดงเพียงจำนวน `ingestion_errors` ที่ `docs/use/status.md:20` ส่วนคำสั่ง SQL ใน Troubleshooting อ่าน `_sync_diagnostics` ที่ `docs/troubleshooting.md:24` ซึ่งเป็นคนละชื่อกับที่สั่งให้ตรวจ

รอบนี้บอกพฤติกรรมข้อมูลเข้าไม่ครบได้ดีขึ้น แต่ขั้นตรวจยังขาดจริง ๆ ขอคำสั่งอ่านอย่างเดียว พร้อมตัวอย่างว่า error อยู่ไฟล์ไหนและเกี่ยวกับส่วนไหนของ transcript ผมยังไม่ได้ขอให้ซ่อมอัตโนมัติ แค่ขอดูสิ่งที่ต้องเก็บก่อนครับ

**ใหม่ 7 — การทดสอบซ้ำบอกให้เปลี่ยนข้อความ แต่ไม่บอกให้จัดการไฟล์ทดสอบอย่างไร**

`docs/getting-started/quick-start.md:55`:

> Use a fresh test phrase if this record already exists.

แต่ `docs/use/ingest.md:54` บอก:

> Fixing bytes in place and rerunning is not a reliable replay: an unchanged file identity/size can be skipped.

ถ้าผมแก้ข้อความใน `test.jsonl` เดิมเป็นข้อความใหม่ที่ยาวเท่าเดิม ผมจะทดสอบการส่งข้อมูล หรือกำลังชนพฤติกรรมข้ามไฟล์ที่คู่มือเตือนไว้เอง? ตัวอย่างยังใช้ `"id":"guide-check"` เดิมด้วย

ขอขั้นทดสอบรอบใหม่ที่ชัดเจนว่าใช้ไฟล์ใหม่ ชื่อใหม่ และ ID ใหม่อย่างไรตามรูปแบบที่รองรับ รวมทั้งกำหนดว่าต้องเห็น `inserted` เพิ่มบน A ก่อนเริ่มจับตา B มิฉะนั้นผมอาจเสียเวลาหาเรื่อง Syncthing ทั้งที่ข้อความใหม่ยังไม่เข้าฐานข้อมูลเลย

**ศัพท์ใหม่ที่ใช้ก่อนอธิบายพอให้ผมทำต่อได้**

ไม่นับศัพท์ที่เคยร้องเรียนในรอบก่อนซ้ำ ตารางนี้ยกข้อความจริงพร้อมจุดที่สะดุด

| ตำแหน่งและข้อความ | ผมติดตรงไหน |
|---|---|
| `docs/getting-started/concepts.md:33` — “Capture scans all eight memory tables and keeps decoded rows in memory.” | `Capture` คือช่วง export ใช่ไหม? `decoded rows` คืออะไร และผมต้องสนใจเพราะใช้ RAM เท่าไร? อยู่หน้า Concepts แต่เริ่มพูดเหมือนผมอ่านโครงสร้างฐานข้อมูลมาแล้ว |
| `docs/getting-started/concepts.md:33` — “Packets, receipts, history and published outbox records are retained without automatic pruning.” | `receipts` กับ `history` เก็บที่ไหน ต่างจาก packets อย่างไร? ถ้าจะให้ผมเฝ้าพื้นที่ดิสก์ ต้องบอก directory ที่ดูด้วย |
| `docs/install/macos.md:65` — “With `--once`, a successful cycle exits `0`, while transient or permanent failure exits `1`.” | `transient` กับ `permanent` เพิ่งโผล่มาเป็นประเภทความล้มเหลว แต่ความหมายและสิ่งที่ต้องทำอยู่ถึง `docs/use/worker.md:111` ขอคำอธิบายสั้น ๆ หรือลิงก์ตรงตั้งแต่ตรงนี้ |
| `docs/install/security.md:51` — “Signed packet encoding and its JSON wire representation must each fit **8 MiB (8 × 1024 × 1024 bytes)**.” | `encoding` กับ `JSON wire representation` เป็นข้อมูลสองขนาดไหน? ผมดูจากขนาดไฟล์ได้หรือไม่ได้? |
| `docs/install/security.md:51` — “Structure is limited to depth 64 and 100,000 encoded values, including dictionary keys.” | `depth` กับ `encoded values` ไม่ได้แปลเป็นสิ่งที่ผู้ใช้ตรวจได้ ต้องนับเองหรือมีข้อความ error บอก? |
| `docs/install/security.md:51` — “The current capture step does not automatically split a large diff.” | `diff` ตรงนี้คือชุด record changes ไม่ใช่ Git diff ใช่ไหม? แล้ว `backlog` ในบรรทัด 53 หมายถึงข้อมูลค้างส่วนใด? |
| `docs/use/worker.md:48` — “The two changes were the event and its ingestion cursor.” | `ingestion cursor` คือเครื่องหมายจำตำแหน่งอ่าน transcript หรืออะไร? นิยามสั้น ๆ ตรงนี้จะช่วยให้เข้าใจทั้งจำนวน changes และเหตุที่ทดสอบซ้ำแล้วถูกข้าม |

**สิ่งใหม่ที่ทำให้ผมอยากเลิกมากที่สุด**

- **ยังไม่ได้เลือกข้อมูล แต่ worker เริ่มแล้ว** — ใหม่ 1 ทำให้ไม่มั่นใจว่าการทดลองติดตั้งจะนำบทสนทนาอะไรไปแชร์บ้าง
- **คู่มือกับ reference บอกกติกาคนละแบบ** — ใหม่ 5 ทำให้ผมต้องตัดสินเองว่าเชื่อหน้าไหน ทั้งที่เพิ่งรู้จักโปรเจกต์คืนนี้
- **มีการทดสอบ แต่ขั้นก่อนทดสอบยังต้องเดา** — ใหม่ 2, 6 และ 7 ทำให้แยกไม่ออกว่าติด shell, ingestion หรือ sync ผมอยากใช้ความจำข้ามเครื่อง ไม่ได้อยากออกแบบชุดวินิจฉัยเอง

**สามการเปลี่ยนแปลงที่ช่วยผมมากที่สุดในรอบนี้**

1. **เพิ่มจุดตรวจแหล่งข้อมูลก่อน worker รอบแรก** ให้เลือก roots และยืนยันขอบเขตการนำเข้าก่อน bootstrap เริ่มทำงาน พร้อมเส้นทางทดลองเฉพาะข้อมูลสังเคราะห์
2. **ทำเส้นทาง Windows ให้ครบและรักษาค่าที่ผู้ใช้เลือก** ตั้งแต่คำสั่งตรง shell ไปจนถึง restart, update และถอน bootstrap โดยไม่หลงกลับ directory หรือ identity ค่าเริ่มต้น
3. **ทำการทดสอบให้แยกสาเหตุได้จริง** ระบุวิธีสร้างรอบทดสอบใหม่ ตรวจว่า A นำเข้าแล้ว เปิดดู ingestion errors ได้ และแก้ reference เรื่อง OS/identity ให้ตรงกัน ผมจะได้ไล่ตรวจตามหลักฐาน ไม่ต้องเดาว่าหน้าไหนพูดถูก