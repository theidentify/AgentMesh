ตรวจครบทั้ง 20 หน้าที่ระบุใน sidebar รวม 988 บรรทัด พร้อมเทียบข้อความในภาพประกอบและคำสั่งที่เกี่ยวข้อง โดยไม่ได้แก้ไขไฟล์ ข้อสังเกตด้าน “ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ” หมายถึงข้อความที่กล่าวเกินขอบเขตคำอธิบายหรือหลักฐานในคู่มือ ไม่ได้หมายความว่าข้อความนั้นผิดทั้งหมด

รายการเรียงตามลำดับหน้าใน sidebar ใช้รูปแบบ: ตำแหน่ง | ประเภท | ข้อความอ้างอิง | ปัญหา | ถ้อยคำเสนอแก้

1. docs/index.md:6 | ความกำกวม | “Share memory between Hermes, OMP, Codex and Claude across your devices, with no cloud memory service.” | อ่านได้ว่ารองรับทั้งการนำเข้าประวัติและการเรียกคืนสำหรับทั้งสี่ระบบ แต่ docs/use/ingest.md:37 ระบุการนำเข้าเพียงสามระบบ โดยไม่มี Hermes | “Recall shared memory from Hermes, OMP, Codex and Claude across your devices. Built-in session ingestion supports OMP, Codex and Claude; no cloud memory service is required.”

2. docs/introduction.md:23 | การเลือกใช้คำ | “**Prerequisites** prepares Python and Syncthing.” | หน้าคู่มือไม่ได้จัดเตรียมซอฟต์แวร์ให้ผู้อ่าน การให้ชื่อหน้าเป็นผู้กระทำทำให้บทบาทคลาดเคลื่อน | “The Prerequisites page explains how to prepare Python and Syncthing.”

3. docs/getting-started/quick-start.md:3 | บทนำไม่ตรงกับเนื้อหา | “Get from a supplied private bootstrap to your first memory recall.” | เส้นทางนี้รับประกันได้เพียงการทดลองค้น ไม่รับประกันว่าจะได้ความทรงจำกลับมา ซึ่งบรรทัด 16 ยอมรับว่าผลลัพธ์อาจว่าง | “Install from a supplied private bootstrap, check sync and run your first recall query.”

4. docs/getting-started/quick-start.md:7 | ความไม่สอดคล้องของศัพท์ | “accept the private exchange” | ใช้ “exchange” แทน “exchange folder” โดยไม่บอกว่าเป็นชื่อย่อ ผู้อ่านใหม่อาจเข้าใจว่าเป็นการยอมรับการเชื่อมต่อหรือธุรกรรม | “accept the private exchange folder in Syncthing”

5. docs/getting-started/concepts.md:7 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “Each device keeps its own memory and shares only small record changes, so the picture below is the whole system.” | ไม่มีเกณฑ์รองรับว่าแต่ละการเปลี่ยนแปลงต้องมีขนาดเล็ก อีกทั้งหน้าเดียวกันระบุว่ามี bootstrap และไฟล์อื่นร่วมด้วย ภาพจึงเป็นภาพย่อ ไม่ใช่ระบบทั้งหมด | “Each device keeps a local database and exchanges record changes. The diagram below shows the main components of routine sync.”

6. docs/getting-started/concepts.md:11 | ความกำกวม | “**Your agents** (Claude, Codex, Hermes, OMP) write session transcripts as they work. The **sync worker** picks up new sessions” | การวางสองประโยคต่อกันทำให้เข้าใจว่า worker นำเข้า transcript ของทั้งสี่ระบบโดยอัตโนมัติ ขัดกับขอบเขตที่ระบุในหน้าการนำเข้า | “Agents write session transcripts as they work. When ingestion is enabled, the sync worker discovers supported OMP, Codex and Claude transcripts.”

7. docs/getting-started/concepts.md:11 | ไวยากรณ์ | “stores them in the local database, **Local database**, and every 60 seconds exports local changes and imports changes from peers.” | ชื่อกล่องในภาพแทรกเป็นคำซ้ำกลางโครงสร้างประโยค ทำให้อ่านสะดุดและไม่ชัดว่าเป็นชื่อหรือคำอธิบาย | “stores them in the local database shown in the diagram, then exports local changes and imports peer changes.”

8. docs/getting-started/concepts.md:11 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “every 60 seconds exports local changes and imports changes from peers” | กล่าวเป็นจังหวะตายตัว ทั้งที่ปรับ interval ได้ และการทำงานรอ interval หลังจบรอบ จึงไม่ใช่การเริ่มรอบใหม่ตรงทุก 60 วินาที | “exports local changes and imports peer changes in repeated cycles, with a default 60-second wait between cycles”

9. docs/getting-started/concepts.md:13 | ช่องว่างทางเหตุผลระหว่างย่อหน้า | “it is also the reason both devices must be online at the same time” | การใช้โฟลเดอร์ร่วมไม่ได้เป็นเหตุเพียงพอที่จะสรุปว่าเครื่องต้นทางและปลายทางต้องออนไลน์พร้อมกัน ข้อจำกัดนี้ขึ้นกับรูปแบบการส่งตรง ซึ่งบรรทัด 35 ระบุได้แม่นยำกว่า | “For direct Syncthing exchange between two devices, both devices must be online at the same time.”

10. docs/getting-started/concepts.md:13 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “The red box is the shortcut people try first” | อ้างพฤติกรรมผู้ใช้ว่าเป็นทางลัดที่ลองก่อนโดยไม่มีข้อมูลรองรับ และไม่จำเป็นต่อคำเตือน | “The red box shows an unsafe shortcut: syncing live database files.”

11. docs/getting-started/concepts.md:13 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “live SQLite files and their WAL/SHM companions corrupt when copied mid-write.” | กล่าวว่าความเสียหายเกิดขึ้นแน่นอนทุกครั้ง ควรอธิบายเป็นความเสี่ยงจากสำเนาที่ไม่สอดคล้องกัน | “Copying live SQLite database files and their WAL/SHM companions during writes can produce an inconsistent or corrupted copy.”

12. docs/getting-started/concepts.md:19 | ความไม่สอดคล้องของศัพท์ | “A device identity combines the sync group UUID with one of three allocation slots: `mac`, `windows` or `linux`.” | ให้นิยาม identity เป็นค่าประกอบ แต่หน้าติดตั้งเรียกค่า `--node` ว่า identity ทั้งที่เป็นเพียง slot และยังไม่เชื่อมศัพท์ “node” กับนิยามนี้ | “A device identity consists of a sync group UUID and a node slot: `mac`, `windows` or `linux`. The `--node` option selects the slot; `<node>` in filenames refers to that slot.”

13. docs/getting-started/concepts.md:39 | ช่องว่างทางเหตุผลระหว่างย่อหน้า | “Peer edits do not write back to PostgreSQL.” | PostgreSQL ปรากฏขึ้นโดยยังไม่ได้อธิบายว่าเกี่ยวข้องกับระบบ SQLite ที่เพิ่งนำเสนออย่างไร ผู้อ่านอาจเข้าใจว่าทุกการติดตั้งมี PostgreSQL | “If you use the optional PostgreSQL mirror, changes imported from peers are not written back to PostgreSQL.”

14. docs/getting-started/prerequisites.md:7 | บทนำไม่ตรงกับเนื้อหา | “**Run the installer**, highlighted, verifies the bootstrap and creates local state.” | ย่อหน้าเปิดบอกว่าหน้านี้เตรียมได้ทั้ง bootstrap และกลุ่มใหม่ แต่คำอธิบายภาพเสนอเส้นทาง bootstrap เป็นเส้นทางทั่วไป | “The diagram shows bootstrap installation. For a new empty group, follow New sync group after completing the applicable prerequisites.”

15. docs/getting-started/prerequisites.md:11 | ช่องว่างทางเหตุผลระหว่างหน้า | “Install Python 3.10 or later with SQLite FTS5 and JSON support.” | หน้านี้ระบุว่าครอบคลุมกลุ่มใหม่ด้วย แต่ docs/install/new-group.md:11 ต้องใช้ Python 3.11 ผู้อ่านอาจเตรียมตามหน้านี้ครบแล้วจึงพบว่ารุ่นยังไม่พอ | “Bootstrap installation requires Python 3.10 or later with SQLite FTS5 and JSON support. Creating a new empty group requires Python 3.11 or later.”

16. docs/install/macos.md:25; docs/install/linux.md:25; docs/install/windows.md:35 | ความกำกวม | “The launcher's default identity must be unused in this group.” | ไม่ระบุค่าเริ่มต้นในจุดที่สั่งให้ตรวจ และสลับเรียกตัวเดียวกันว่า installer, wrapper และ launcher ผู้อ่านต้องอนุมานค่าจากชื่อฐานข้อมูลด้านล่าง | “The platform setup wrapper selects the `mac`, `linux` or `windows` node slot on the corresponding platform. Confirm that the selected slot is unused in this group.”

17. docs/install/macos.md:61; docs/install/linux.md:61 | ความไม่สอดคล้องของศัพท์ | “installations with signed security” | “signed security” ไม่ใช่ศัพท์ที่นิยามไว้ หน้าอื่นใช้ “Signed sync”, “signed installations” และ “signed policy” | “installations with signed sync enabled”

18. docs/install/windows.md:55 | ความกำกวม | “Run the same CMD after login to resume.” | “CMD” อาจหมายถึง Command Prompt, คำสั่งก่อนหน้า หรือไฟล์ `.cmd` อีกทั้งไม่ระบุว่าข้อความนี้ใช้กับผู้ที่เริ่มผ่าน launcher | “If you installed with START-WINDOWS.cmd, run START-WINDOWS.cmd again after login to resume.”

19. docs/install/windows.md:73 | ความกำกวม | “Native Windows production verification remains outstanding in the repository's documented boundary.” | ไม่ระบุรุ่น วันที่ หรือขอบเขตการทดสอบ และคำว่า “documented boundary” ไม่บอกว่าจะหาเอกสารใด จึงอ่านได้ทั้งว่ายังไม่เคยทดสอบ Windows และว่าเฉพาะขั้นตอนนี้ยังไม่ยืนยัน | “This guide does not establish that these steps have been verified on your Windows host. Check command execution, firewall behavior and local permissions there.”

20. docs/install/background-service.md:79 | การเลือกใช้คำ | “existing-install program installation” | คำนามซ้อนกันไม่ช่วยให้เข้าใจว่างานที่ต้องทำเพิ่มคืออะไร และ “owned program state” ในประโยคถัดมาก็ยังไม่มีนิยามสำหรับผู้ใช้ | “First install the standalone Windows program for your existing AgentMesh database and runtime, following the Windows installation reference.”

21. docs/install/background-service.md:93 | ความกำกวม | “For signed policy, omit it” | สรรพนาม “it” บังคับให้ย้อนหา flag และไม่ชัดว่าต้องละออกจากคำสั่งใด ในขั้นตอนที่ผลขึ้นกับ policy ควรเขียนตรงตัว | “For an installation with signed sync required, omit `--legacy-drained` from the enable command.”

22. docs/install/new-group.md:13 | ความกำกวม | “The new local directory must not exist, its parent must already exist, and neither directory may contain the other.” | มีทั้ง exchange, local directory และ parent อยู่ในบริบท แต่ “neither directory” ไม่ระบุว่าหมายถึงคู่ใด หากอ่านเป็น local directory กับ parent จะขัดกันเอง | “The new local directory must not exist, and its parent must already exist. The local directory and exchange directory must not be nested inside one another.”

23. docs/install/new-group.md:27 | ความไม่สอดคล้องของศัพท์ | “The literal value after `--node` selects a device identity” | ตามนิยามใน Concepts ค่านี้เลือกเฉพาะ slot ไม่ได้เลือกรหัสกลุ่มหรือ identity ทั้งชุด | “The value after `--node` selects the node slot. The command creates a new sync group UUID.”

24. docs/install/new-group.md:38 | ช่องว่างทางเหตุผลระหว่างย่อหน้า | “arrange a private bootstrap with the operator” | หน้านี้เป็นทางเข้าของผู้ที่ไม่มี bootstrap แต่จู่ ๆ สมมติว่ามี operator อยู่แล้ว โดยไม่บอกว่าผู้สร้างกลุ่มอาจต้องรับบทบาทนี้เองหรือควรไปขั้นตอนใด | “To add a peer, have the person administering this group prepare a private bootstrap. If you administer the group yourself, follow the bootstrap procedure in the sync reference.”

25. docs/install/new-group.md:40 | ช่องว่างทางเหตุผลระหว่างหน้า | “Next, [ingest agent sessions](../use/ingest) manually to give this empty database something to recall” | หน้านี้ยังไม่ติดตั้งแอปหรือเริ่ม worker แต่หน้าปลายทางส่งต่อไปยัง status และ worker ที่สมมติว่าติดตั้งแล้ว เส้นทางสร้างกลุ่มจึงขาดขั้นตอนเชื่อมไปสู่การใช้งานจริง | “Next, ingest sessions manually from the repository's `memory-sync` directory. Before continuing to sync checks, complete application setup and start a worker using this group's runtime.”

26. docs/install/security.md:11 | ความไม่สอดคล้องของศัพท์ | “their group, sender and device identity” | “sender” ปรากฏโดยไม่อธิบายว่าต่างจาก device identity หรือ node slot อย่างไร ทั้งที่เป็นความแตกต่างสำคัญต่อความไว้ใจ | “Signatures authenticate packet metadata, including the sync group, sender identifier and node slot. The sender identifier is distinct from the node slot.”

27. docs/install/security.md:37 | ความกำกวม | “when policy is required, the wizard is active, pairing is approved and prerequisites are ready” | “policy is required” อ่านได้ว่าจำเป็นต้องมี policy ใดก็ได้ แทนที่จะหมายถึงค่า policy เฉพาะ และ “active” อาจหมายถึงโปรเซส wizard กำลังทำงาน | “when the signing policy is `required`, the wizard state is `active`, pairing is `approved` and prerequisites are ready”

28. docs/install/update-remove.md:11 | ช่องว่างทางเหตุผลระหว่างหน้า | “On Windows, stop the old foreground worker and rerun **START-WINDOWS.cmd**” | บอกให้หยุดเฉพาะ foreground worker ทั้งที่คู่มือรองรับ managed worker แบบ detached ด้วย จึงไม่ครอบคลุมเส้นทางที่หน้าอื่นสอน | “On Windows, stop the current worker using its foreground, managed or service controls before rerunning START-WINDOWS.cmd.”

29. docs/install/update-remove.md:19 | การเลือกใช้คำ | “an existing owned program installation” | “owned” อาจหมายถึงเจ้าของบัญชี เจ้าของไฟล์ หรือโปรแกรมที่ installer จัดการ คำอธิบายเรื่อง ownership มาช้ากว่าเงื่อนไขใช้งาน | “an existing program installation tracked and managed by the Windows installer”

30. docs/use/worker.md:33 | ความกำกวม | “`once` forces the configured summary schedule” | “forces the schedule” ไม่ชัดว่าเรียก summarization ทันที เปลี่ยนตารางเวลา หรือเปิดฟีเจอร์ที่ปิดอยู่ โดยเฉพาะเมื่อย่อหน้าก่อนหน้าบอกว่ากลุ่มใหม่ปิด summarization | “When summarization is enabled, `once` requests it immediately rather than waiting for its scheduled time; managed startup follows the configured schedule.”

31. docs/use/worker.md:59 | การเลือกใช้คำ | “waits for the lifetime lock to drain” | ใช้คำเปรียบ “drain” กับ lock โดยไม่อธิบายสิ่งที่ผู้ใช้สังเกตได้ และใช้คำเดียวกันกับการหยุด worker และการจัดการ unsigned history ในหน้าอื่น | “waits for the worker to release its lifetime lock”

32. docs/use/recall.md:7 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “The **agent** reads its AgentMesh rule and runs the exact command it contains.” | การติดตั้งคำแนะนำไม่ได้รับประกันว่า agent จะอ่านหรือทำตามทุกครั้ง ควรบอกสิ่งที่ rule สั่งและให้ผู้อ่านตรวจการเรียกใช้จริง | “The installed AgentMesh rule instructs the agent to run the configured recall command. Verify that your agent loads the rule and invokes that command.”

33. docs/use/recall.md:7 | ความไม่สอดคล้องของศัพท์ | “evidence IDs” | หน้าแรกใช้ “source IDs” และคำเตือนบรรทัด 47 ก็ใช้ “source IDs” โดยยังไม่บอกว่าเป็นสิ่งเดียวกันหรือเป็นรหัสคนละชนิด | “identifiers linking results to their supporting source records”

34. docs/use/recall.md:52 | ช่องว่างทางเหตุผลระหว่างย่อหน้า | “SQLite and PostgreSQL can differ in tokenization, candidate selection and tie ordering.” | หัวข้อนี้เปลี่ยนจากการติดตั้ง recall บน SQLite ไปเปรียบเทียบ backend โดยไม่อธิบายว่าผู้อ่านคนใดจำเป็นต้องสนใจ | “If you are comparing this SQLite recall path with an existing PostgreSQL integration, tokenization, candidate selection and tie ordering may differ.”

35. docs/use/ingest.md:7 | หัวข้อไม่ตรงกับเนื้อหา | “Ingest everything found” | เนื้อหาระบุข้อยกเว้นหลายอย่าง ได้แก่ subagents, symlink และบรรทัดที่ยังไม่สมบูรณ์ จึงไม่ใช่ทุกสิ่งที่ค้นพบ | “Discover and ingest supported sessions”

36. docs/use/ingest.md:47 | ช่องว่างทางเหตุผลระหว่างย่อหน้า | “Custom session locations need explicit configuration or a manual command.” | ไม่บอกว่า configuration อยู่ที่ไหนหรือใช้หัวข้อใด ผู้อ่านอาจเข้าใจว่า `--root` ในคำสั่งก่อนหน้าจะบันทึกค่าให้ worker ด้วย | “The `--root` option applies to that manual ingestion command. To change automatic discovery, configure the worker's `workflow.json` using the Workflow reference.”

37. docs/use/ingest.md:59 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “Once sessions are ingested locally, the worker shares them with your other devices.” | ทำให้ดูว่าการนำเข้าสำเร็จนำไปสู่การแบ่งปันสำเร็จโดยอัตโนมัติ ทั้งที่ยังต้องมี worker, transport และการยอมรับ packet อีกทั้งสิ่งที่ส่งคือ records ไม่ใช่ไฟล์ session | “After local ingestion, a running worker can export the resulting records. Peer devices import them when transport, worker availability and packet validation permit.”

38. docs/use/status.md:7 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “Each cycle runs in arrow order.” | ภาพเป็นแบบย่อ แต่ข้อความกล่าวครอบคลุมทุก cycle โดยไม่รวมเงื่อนไขการเปิด ingestion, ขั้น summarization หรือความเป็นอิสระของการส่งไฟล์ผ่าน Syncthing | “The diagram summarizes the basic sync flow. Ingestion depends on workflow settings, Syncthing transfers files independently, and configured summarization may add further work.”

39. docs/use/status.md:23 | ความไม่สอดคล้องของศัพท์ | “using the database's device identity” | เมื่อตามด้วย `status/<node>.json` ผู้อ่านอาจแทนค่าด้วย identity ทั้งชุดตามนิยาม UUID + slot ทั้งที่ชื่อไฟล์ใช้ node slot | “using the database's node slot as `<node>`”

40. docs/use/status.md:28 | ความกำกวม | “The worker's one-cycle command” | มีหลายคำสั่งที่ทำหนึ่งรอบ ได้แก่ `once`, `worker-run --once` และการเรียก script ด้วย `--once` แต่ย่อหน้านี้อ้างพฤติกรรม exit code โดยไม่ระบุคำสั่ง | “The `agentmesh.py once` action returns failure when its cycle report records conflicts or invalid packets.”

41. docs/use/status.md:53 | ข้อกล่าวอ้างที่ไม่มีหลักฐานรองรับ | “To browse the same information in a dashboard” | “the same information” ทำให้คาดหวังข้อมูลเทียบเท่าทุกคำสั่ง ทั้งที่บรรทัด 25 เองเรียกข้อมูลใน Insight ว่า restricted projection | “To browse selected memory and sync observations in a dashboard, open Insight.”

42. docs/use/insight.md:28 | ความไม่สอดคล้องของศัพท์ | “the runtime profile directory containing your metrics and job files” | “runtime profile directory” ไม่เคยนิยาม และเสี่ยงสับสนกับไดเรกทอรีที่มี `runtime.json` ขณะที่ `--home` ใช้ค่าเริ่มต้นจาก Hermes profile | “Use `--sqlite` for your database and `--home` for the profile directory containing metrics and job files. The profile directory is not necessarily the directory containing `runtime.json`.”

43. docs/use/insight.md:35 | การเลือกใช้คำ | “Authority labels require explicit backend configuration” | “Authority labels” และ “backend configuration” ไม่อธิบายว่าผู้ใช้จะเห็นอะไรหรือเหตุใดต้องตั้งค่า เป็นศัพท์ภายในที่แทรกในขั้นเริ่มใช้งาน | “To label a backend as authoritative in Insight, configure that role explicitly using the Insight reference.”

44. docs/troubleshooting.md:31 | น้ำเสียงไม่สอดคล้อง | “do not overwrite either database to repair this casually.” | “casually” ประเมินท่าทีของผู้ใช้แทนที่จะระบุข้อห้ามเชิงปฏิบัติ น้ำเสียงต่างจากคำแนะนำตรงไปตรงมาในส่วนอื่น | “Do not overwrite either database during recovery; preserve both for operator review.”

45. docs/privacy.md:23 | ความกำกวม | “File hashes detect accidental corruption, not malicious replacement.” | hash ตรวจพบเนื้อหาที่เปลี่ยนได้หากมีค่าอ้างอิงที่เชื่อถือได้ ข้อจำกัดจริงคือ hash เพียงอย่างเดียวไม่รับรองผู้ส่ง และผู้โจมตีอาจแทนที่ทั้งไฟล์กับ manifest | “File hashes detect changes relative to a trusted expected hash. They do not authenticate the sender or protect against replacement of both the file and its checksum manifest.”

46. docs/reference/index.md:3 | บทนำไม่ตรงกับเนื้อหา | “each with its own verification limits” | หน้านี้จัดกลุ่มลิงก์ตามผู้อ่าน แต่ไม่ได้แสดงหรือเปรียบเทียบขอบเขตการตรวจสอบตามที่บทนำชวนให้คาดหวัง | “This index separates user instructions, operator procedures and developer material. Consult each linked document for its verification limits.”

โดยรวม คู่มือมีโครงสร้างอ่านง่าย หน้าแต่ละหน้าค่อนข้างสั้น และแยกการส่งไฟล์ออกจากการนำเข้า records ได้ดี คำเตือนเรื่องข้อมูลส่วนตัว การเรียกคืนแบบอ่านอย่างเดียว และการไม่ใช้สถานะโปรเซสเป็นหลักฐานว่างานสำเร็จมีความสอดคล้องสูง ไม่พบข้อสะกดผิดที่ชัดเจน ปัญหาหลักอยู่ที่ความแม่นยำของศัพท์และการเชื่อมขั้นตอน มากกว่าไวยากรณ์พื้นฐาน

ในมาตรฐานการตรวจวิทยานิพนธ์ ยังควรปรับก่อนถือว่าคู่มือเป็นเอกสารที่ผู้ใช้ใหม่ทำตามได้โดยไม่ต้องอนุมาน โดยเฉพาะนิยาม identity–slot–node, ความแตกต่างระหว่างการรองรับ recall กับ ingestion, เงื่อนไขการทำงานของ worker และเส้นทางจากกลุ่มใหม่ไปสู่ระบบที่ใช้งานได้จริง การแก้สี่เรื่องนี้พร้อมลดถ้อยคำเด็ดขาดจะเพิ่มทั้งความเป็นเอกภาพ ความน่าเชื่อถือ และความอ่านเข้าใจได้ตลอดทั้งเล่มอย่างชัดเจน