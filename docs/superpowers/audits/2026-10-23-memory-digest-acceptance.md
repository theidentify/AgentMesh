# Audit: memory digest ในมุมของ acceptance (อ่านอย่างเดียว)

วันที่ 2026-10-23 — โค้ดที่ `main` 418e5f1, ข้อมูลจาก `~/.agentmesh/jobs/summary-metrics.jsonl` (449 runs, 2026-10-21 01:07 ถึง 2026-10-22 14:13 UTC) ไม่ได้แก้โค้ด ข้อมูล หรือ job ใด ๆ

## ที่มา

ชุดวิจัย intent–evidence continuity (ปิดแล้ว 2026-10-23) พบว่าความผิดที่เกิดจริงซ้ำทุกรอบคือ **representation** ไม่ใช่การตีความความหมาย และได้บทเรียนสามข้อ: (5) ตัด field ที่โมเดลต้องพิมพ์ซ้ำสิ่งที่ระบบรู้อยู่แล้ว, (3) สิ่งที่รับไม่สำเร็จต้องเป็นข้อค้าง ไม่ใช่วนส่งซ้ำ, (6) operation ที่ย้อนไม่ได้ต้องยืนยันก่อน ใน production มีจุดเดียวที่ LLM สร้าง structured output แล้วระบบนำไปใช้ คือ memory digest (`bounded_protocol.py`, `bounded_digest.py`; pipeline `agentmesh-bounded-v1`)

## สิ่งที่ production ทำดีอยู่แล้ว

- Schema แยกตาม scope และล็อก `scope`/`project`/`scope_key` ด้วย enum ([bounded_protocol.py:150](../../../memory-sync/bounded_protocol.py#L150), [:153](../../../memory-sync/bounded_protocol.py#L153))
- การอัปเดต item เดิมไม่ต้องให้โมเดลพิมพ์ identity ซ้ำ ระบบเติมจากของเดิมเอง ([:69](../../../memory-sync/bounded_protocol.py#L69))
- ตรวจ quote ว่าอยู่ใน event จริง และไม่ยอมรับ batch ที่ไม่ผ่านการตรวจ
- Explicit conflict แยกออกเป็น ConflictError และไม่ retry อัตโนมัติ

## ข้อค้นพบ

### F1 — Field ซ้ำซ้อนทำให้ batch ถูกปฏิเสธ (สูง)

`source_event_ids` ต้องเท่ากับชุด `evidence[].event_id` ทุกตัว ([:102](../../../memory-sync/bounded_protocol.py#L102)) ทั้งที่สองค่านี้บอกสิ่งเดียวกัน เป็นแบบเดียวกับ `status` ซ้ำซ้อนในงานวิจัย ซึ่งทำให้ proposal ที่ถูกถูกปฏิเสธ 30–45% และเมื่อตัดออก (OPSPEC) invalid ลดเหลือ 0

**ข้อมูลจริง:** `evidence coverage mismatch` 2 runs, USD 0.16

**ทางแก้ที่เสนอ:** ตัด `source_event_ids` ออกจาก output schema แล้ว derive จาก `evidence` หรือในทางกลับกัน

### F2 — Schema อนุญาตสิ่งที่ validator ห้าม (สูง)

Session-scope variant ให้เลือก `scope_key` ได้จากทุก session ใน batch (enum ที่ [:153](../../../memory-sync/bounded_protocol.py#L153)) แต่ validator บังคับว่า event ที่อ้างทุกตัวต้องอยู่ใน session เดียวกับ `scope_key` ([:109](../../../memory-sync/bounded_protocol.py#L109)) ข้อจำกัดนี้แสดงใน schema ไม่ได้ โมเดลจึงสร้าง output ที่ผ่าน schema แต่ไม่ผ่าน validator

**ข้อมูลจริง:** `unsupported session scope` 7 runs, USD 0.50 (มากที่สุดในกลุ่มที่ไม่ใช่ quota)

**ทางแก้ที่เสนอ:** derive `scope_key` ของ session จาก event ที่อ้าง (ปฏิเสธเมื่อ event ที่อ้างมาจากหลาย session) หรือแยก variant ต่อ session ให้ `source_event_ids`/`evidence.event_id` มี enum เฉพาะ event ของ session นั้น

### F3 — Item ใหม่ใช้ key เดิมได้ (กลาง)

Pattern ของ `memory_key` สำหรับ item ใหม่ ([:150](../../../memory-sync/bounded_protocol.py#L150)) ไม่กัน key ที่มีอยู่แล้ว โมเดลจึงใช้ variant ของ item ใหม่กับ key เดิมได้ แล้วไปชน `stable key identity changed` ([:85](../../../memory-sync/bounded_protocol.py#L85))

**ข้อมูลจริง:** 3 runs, USD 0.10

**ทางแก้ที่เสนอ:** ถ้า key อยู่ใน `related` ให้ถือเป็น update แล้วเติม identity จากของเดิมเสมอ (ขยายพฤติกรรมของ [:69](../../../memory-sync/bounded_protocol.py#L69)) โดยไม่ต้องไว้ใจ kind/scope ที่โมเดลส่งมา

### F4 — Item ผิดตัวเดียวทำให้ทั้ง batch ล้ม แล้ววนส่งซ้ำ (สูง)

`validate()` ปฏิเสธทั้ง payload ทันทีที่เจอ item ผิดตัวเดียว ระบบ retry ด้วยโมเดลเดิมหนึ่งครั้ง ([bounded_digest.py:266](../../../memory-sync/bounded_digest.py#L266)) และ coverage ไม่ขยับ รอบถัดไปจึงส่ง batch เดิมอีก

**ข้อมูลจริง:** วันที่ 22 ต.ค. 01:32–05:20 UTC highwater `2199023599495` ล้มติดกัน 12 runs ด้วย F1–F3 ทุกครั้งเป็นการเรียกที่เสียเงิน รวม ~USD 0.67 และ memory หยุดเดินราว 4 ชั่วโมง failed runs ทั้งหมดใช้เงิน USD 0.77 จาก 6.72 (11%)

**ทางแก้ที่เสนอ (ต้องออกแบบก่อน):** แยก item ที่ไม่ผ่านไปเป็น quarantine/pending พร้อมเหตุผล แล้ว commit item ที่ผ่าน ส่วน event ที่ item ถูกกักไว้อ้างถึง ต้องไม่ถูกนับเป็น covered จนกว่าจะสะสาง (ตามบทเรียนที่ 3 ข้อค้างต้องอ้างกลับได้ ไม่ใช่ทิ้งเงียบ) อีกทางที่ง่ายกว่าคือหยุด retry เมื่อ batch เดิมล้มด้วยเหตุผลเดิมเกิน N ครั้ง แล้วแจ้งเตือน

### F5 — โมเดลลบหรือแทนที่ memory เดิมได้ทันทีและเขียนทับเนื้อหา (กลาง ต้องตรวจเพิ่ม)

Item ที่ update สามารถตั้ง `status` เป็น `deleted`/`superseded` ได้ upsert เขียนทับทั้ง `content` และ `status` ([bounded_digest.py:112-113](../../../memory-sync/bounded_digest.py#L112)) และ recall อ่านเฉพาะ `status='active'` ([recall_memory.py:180](../../../memory-sync/recall_memory.py#L180)) memory นั้นจึงหายจาก recall ทันทีโดยไม่มีการยืนยัน metadata เก็บ `evidence_history` แต่ไม่เก็บ content เดิม (ยังไม่ได้ตรวจว่า profile log เก็บสำเนาเดิมไว้หรือไม่)

**ทางแก้ที่เสนอ:** อย่างน้อยเก็บ content/status เดิมไว้ใน history ก่อนเขียนทับ และพิจารณาให้ `deleted` เป็นข้อเสนอที่รอยืนยัน (บทเรียนที่ 6) ส่วน `superseded`/`resolved` ยอมให้ทำได้เมื่อมีหลักฐาน

### F6 — Quota 429 เป็นสาเหตุหลักแต่อยู่นอกขอบเขต

26 จาก 46 failed runs เป็น 429 ไม่เสียเงิน และ coverage ไม่ขยับ ตรงกับ memory ที่ตกลงไว้ว่าให้แจ้งเตือนต่อ จึงไม่เสนอแก้ในงานนี้ ส่วน `model worker unavailable or invalid` 8 runs (08:31–08:57 ติดกัน 6 ครั้ง) ยังไม่รู้สาเหตุ ต้องดู worker log ก่อน

## สิ่งที่ไม่ควรทำ

- อย่าเพิ่มโมเดลตัวที่สองมาตรวจ digest งานวิจัย H24 แสดงว่า verifier พลาดร่วมกับ proposer เมื่อข้อความกำกวม
- อย่าซ่อม output ของโมเดลแบบเงียบ ๆ ให้ derive จาก field ที่เชื่อถือได้และบันทึกทุกครั้งที่ derive

## ลำดับที่เสนอ

1. **F1 + F3** แก้ไม่มาก ไม่เปลี่ยนความหมายของ memory เป็นการตัด field ที่ซ้ำซ้อน และ derive identity เพิ่ม แล้วเพิ่ม test ที่จำลอง failure จริง
2. **F2** derive session `scope_key` หรือแยก variant
3. **F4** ออกแบบ quarantine หรือ circuit breaker ก่อนลงมือ เพราะกระทบ semantics ของ coverage
4. **F5** ตรวจ profile log ก่อน แล้วค่อยเลือกระหว่างเก็บ history กับยืนยันการลบ
5. หลังแก้: วัดสัดส่วน failed runs ที่ไม่ใช่ 429 จาก `summary-metrics.jsonl` ช่วงเวลาเท่ากัน (ฐานเดิม 20/449 = 4.5% ทั้งหมดเสียเงินและหยุด memory)

## ข้อจำกัดของ audit นี้

ข้อมูลครอบคลุมเพียง ~37 ชั่วโมง ไม่ได้อ่าน payload ที่ล้ม (metrics เก็บเพียงข้อความ error) จำนวนน้อย จึงบอกได้ว่าปัญหาเกิดจริงและมีต้นทุนจริง แต่ยังไม่ใช่อัตราที่เชื่อถือได้ระยะยาว
