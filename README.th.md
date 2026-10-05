# dbasim — ฝึกแก้ปัญหา Oracle จากสถานการณ์จริง

[English](README.md)

dbasim สร้าง "เหตุการณ์ DB มีปัญหา" ขึ้นใน Oracle Database Free บนเครื่องของคุณเอง
มีแอปจำลองที่มีคนใช้งานอยู่ตลอด ให้คุณหาสาเหตุ แก้ปัญหา แล้วให้ระบบตรวจว่าแก้ถูกหรือไม่

> ⚠️ dbasim จะสร้างและลบ user / tablespace ใน DB ที่ชี้ไป ใช้กับ Oracle Free ในเครื่องตัวเองเท่านั้น
> โปรแกรมจะไม่ยอมทำงานกับ DB ที่ไม่ใช่ Free และจะไม่ลบ user `SHOP` ที่ไม่ได้สร้างเอง

## สิ่งที่ต้องมี

- Docker (Windows ใช้ Docker Desktop + WSL2)
- RAM ว่างประมาณ 3GB และ disk ว่าง 10GB
- Python 3.9 ขึ้นไป

## 1. เปิด Oracle Database Free

```bash
docker run -d --name dbasim-oracle -p 1521:1521 \
  -e ORACLE_PWD=ChangeMe123 \
  container-registry.oracle.com/database/free:latest

docker logs -f dbasim-oracle   # รอจนเห็น DATABASE IS READY TO USE!
```

การดาวน์โหลดและใช้ Oracle Database Free อยู่ภายใต้
[Oracle Free Use Terms and Conditions](https://www.oracle.com/downloads/licenses/oracle-free-license.html)
ระหว่างคุณกับ Oracle โดยตรง

## 2. ติดตั้ง dbasim

```bash
pip install dbasim
```

## 3. ตั้งค่า

```bash
export DBASIM_ADMIN_PASSWORD=ChangeMe123          # Windows PowerShell: $env:DBASIM_ADMIN_PASSWORD="ChangeMe123"
export DBASIM_DSN=localhost:1521/FREEPDB1           # ค่า default
export DBASIM_SCALE=0.3                             # ถ้าเครื่องช้า ลดขนาดข้อมูลลง
dbasim lang th                                      # ภาษาไทย (ค่าเริ่มต้นเป็นอังกฤษ)
dbasim doctor
```

## 4. เล่น

| คำสั่ง | ทำอะไร |
| --- | --- |
| `dbasim list` | ดูโจทย์ทั้งหมดและคะแนน |
| `dbasim start s01` | เริ่มโจทย์ (สร้างปัญหา + เปิดแอปจำลอง) |
| `dbasim logs` | ดู log ของแอป เหมือนตอนรับ ticket |
| `dbasim status` | เวลาที่ใช้ คะแนน และรหัสผ่าน user SHOP |
| `dbasim hint` | ขอคำใบ้ (หัก 15 คะแนนต่อครั้ง) |
| `dbasim check` | ตรวจคำตอบ ผ่านแล้วจะเห็นเฉลยแบบ DBA อาวุโส |
| `dbasim solution --yes` | ยอมแพ้ ดูเฉลย (0 คะแนน) |
| `dbasim lang th` | เปลี่ยนเป็นภาษาไทย ค่าเริ่มต้นเป็นอังกฤษ (`dbasim lang en` กลับเป็นอังกฤษ หรือตั้ง `DBASIM_LANG`) |
| `dbasim reset` | ล้างทุกอย่างที่ dbasim สร้าง และคืนค่าที่โจทย์ชวนให้แก้ (cursor_sharing, idle timeout, logon trigger ที่ตั้ง cursor_sharing) |

ใช้เครื่องมืออะไรแก้ก็ได้ เช่น SQL*Plus, SQLcl, SQL Developer ต่อด้วย SYSTEM เข้า `FREEPDB1`
ทุกโจทย์แก้ได้ด้วยเครื่องมือที่มีใน Oracle Free (`v$` views, `DBMS_XPLAN`, trace)
ไม่ต้องใช้ AWR/ASH

## โจทย์ที่มีตอนนี้

| ID | โจทย์ | ระดับ | แผน |
| --- | --- | --- | --- |
| s01 | หน้าค้นหาออเดอร์ช้า | ง่าย | ฟรี |
| s02 | Report ปิดเดือนช้าหลังย้ายข้อมูล | ง่าย | ฟรี |
| s03 | Batch กลางคืนล้มทุกคืน | ง่าย | ฟรี |
| s04 | หน้าจอบันทึกการชำระเงินค้าง | กลาง | Pro (เร็วๆ นี้) |
| s05 | CPU พุ่งหลังปล่อยแอปเวอร์ชันใหม่ | กลาง | Pro (เร็วๆ นี้) |

3 โจทย์แรกเล่นฟรีตลอด ไม่ต้องสมัครสมาชิกและไม่ต้องใช้ license key ส่วนโจทย์ Pro ยังไม่เปิดขาย (เร็วๆ นี้) กด Watch ที่ repo นี้ไว้เพื่อรับข่าวตอนเปิดตัว

เจอปัญหาหรืออยากเสนอโจทย์ใหม่ เปิด issue ได้ที่ https://github.com/kulchartt/oracledbasim/issues

## ทดสอบทุกโจทย์กับ Oracle จริง (Windows คลิกเดียว)

ดับเบิลคลิก `run-selftest.bat` สคริปต์จะ:
1. เช็ค Docker และ Python
2. ดาวน์โหลดและเปิด Oracle Database Free (ครั้งแรกโหลดหลาย GB)
3. ติดตั้ง dbasim ใน `.venv`
4. รัน `dbasim selftest` ซึ่งทำทุกโจทย์: ฝังปัญหา → ตรวจว่าต้องไม่ผ่าน → แก้ด้วยวิธีเฉลย → ตรวจว่าต้องผ่าน

ผลจะอยู่ใน `selftest-result.txt` และ `selftest-dbasim.txt`

## สำหรับนักพัฒนา

```bash
pip install -e ".[test]"
pytest
```

โครงของโจทย์อยู่ใน `dbasim/scenarios/` แต่ละข้อมี `setup` (ฝังปัญหา), `workload_step`
(ผู้ใช้จำลอง), `collect` (วัดค่าใน DB) และ `evaluate` (ตัดสินแบบ pure Python ทดสอบได้โดยไม่ต้องมี Oracle)
