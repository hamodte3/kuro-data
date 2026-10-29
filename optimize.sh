#!/usr/bin/env bash

# ألوان التنسيق للطرفية
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${CYAN}=== بدء فحص الاتصال بالجهاز ===${NC}"

# 1. التحقق من اتصال ADB
if ! adb get-state 1>/dev/null 2>&1; then
    echo -e "${RED}[!] لم يتم العثور على جهاز متصل عبر ADB. تأكد من تفعيل USB Debugging.${NC}"
    exit 1
fi

DEVICE_MODEL=$(adb shell getprop ro.product.model | tr -d '\r')
echo -e "${GREEN}[+] متصل بنجاح: ${DEVICE_MODEL}${NC}"

# قراءة الحرارة المبدئية
TEMP_RAW=$(adb shell dumpsys battery | grep temperature | awk '{print $2}' | tr -d '\r')
TEMP_C=$(awk "BEGIN {print $TEMP_RAW / 10}")
echo -e "${YELLOW}[i] حرارة البطارية الحالية: ${TEMP_C}°C${NC}\n"

# 2. تنظيف السجلات والكاش
echo -e "${CYAN}[1/5] تصفير مخزن Logcat وتفريغ كاش النظام الديناميكي...${NC}"
adb logcat -c
adb shell pm trim-caches 999G > /dev/null 2>&1
echo -e "${GREEN}✓ تم تفريغ السجلات والكاش بنجاح.${NC}\n"

# 3. إدارة التطبيقات المجمدة حالياً (اختياري)
echo -e "${CYAN}[2/5] فحص التطبيقات المجمدة حالياً (Disabled Packages)...${NC}"
FROZEN_DATA=$(adb shell pm list packages -d --user 0 2>/dev/null | sed 's/^package://' | tr -d '\r' | grep -v '^$')

if [ -z "$FROZEN_DATA" ]; then
    echo -e "${GREEN}[i] لا توجد تطبيقات مجمدة حالياً على الجهاز.${NC}\n"
else
    declare -a FROZEN_LIST
    f_index=1

    echo -e "${BOLD}التطبيقات المعطلة حالياً:${NC}"
    printf "%-5s %-40s\n" "الرقم" "اسم الحزمة"
    echo "--------------------------------------------------------"

    while read -r pkg; do
        [ -z "$pkg" ] && continue
        FROZEN_LIST[$f_index]="$pkg"
        printf "%-5s %-40s\n" "[$f_index]" "$pkg"
        ((f_index++))
    done <<< "$FROZEN_DATA"

    echo ""
    read -rp "هل ترغب بإلغاء تجميد أي تطبيق من القائمة؟ (y/N): " want_unfreeze
    if [[ "$want_unfreeze" =~ ^[Yy]$ ]]; then
        read -rp "أدخل رقم التطبيق لإلغاء تجميده: " unfreeze_choice
        if [[ "$unfreeze_choice" =~ ^[0-9]+$ ]] && [ "$unfreeze_choice" -ge 1 ] && [ "$unfreeze_choice" -lt "$f_index" ]; then
            target_unfreeze="${FROZEN_LIST[$unfreeze_choice]}"
            echo -e "${YELLOW}[!] جاري تفعيل: ${target_unfreeze}...${NC}"
            adb shell pm enable "$target_unfreeze" > /dev/null 2>&1
            echo -e "${GREEN}✓ تم إلغاء تجميد ${target_unfreeze} وعاد للعمل.${NC}\n"
        fi
    else
        echo -e "${CYAN}تم تخطي إلغاء التجميد.${NC}\n"
    fi
fi

# 4. مراقبة استهلاك المعالج (عرض البيانات فقط، والتعطيل اختياري بالكامل)
echo -e "${CYAN}[3/5] رصد استهلاك أنوية المعالج (CPU Profiling)...${NC}"
RAW_CPU_DATA=$(adb shell dumpsys cpuinfo | grep -E "^ +[0-9]+(\.[0-9]+)?%" | grep -v "TOTAL:" | head -n 7)

declare -a PKG_LIST
c_index=1

echo -e "\n${BOLD}أعلى العمليات استهلاكاً للمعالج حالياً:${NC}"
printf "%-5s %-10s %-35s\n" "الرقم" "النسبة" "اسم العملية / الحزمة"
echo "--------------------------------------------------------"

while read -r line; do
    [ -z "$line" ] && continue
    cpu_val=$(echo "$line" | awk '{print $1}')
    pkg_val=$(echo "$line" | sed -E 's/.*[0-9]+\/([^:]+):.*/\1/' | awk '{print $1}' | tr -d '\r')

    PKG_LIST[$c_index]="$pkg_val"
    printf "%-5s %-10s %-35s\n" "[$c_index]" "$cpu_val" "$pkg_val"
    ((c_index++))
done <<< "$RAW_CPU_DATA"

echo ""
# الخطوة أصبحت اختيارية تماماً بسؤال y/N صريح
read -rp "هل ترغب بتجميد أي تطبيق من القائمة أعلاه؟ (y/N): " want_freeze
if [[ "$want_freeze" =~ ^[Yy]$ ]]; then
    read -rp "أدخل رقم التطبيق لتجميده: " freeze_choice
    if [[ "$freeze_choice" =~ ^[0-9]+$ ]] && [ "$freeze_choice" -ge 1 ] && [ "$freeze_choice" -lt "$c_index" ]; then
        target_freeze="${PKG_LIST[$freeze_choice]}"
        echo -e "${YELLOW}[!] جاري إيقاف وتجميد: ${target_freeze}...${NC}"
        adb shell am force-stop "$target_freeze"
        adb shell pm disable-user --user 0 "$target_freeze" > /dev/null 2>&1

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ تم تجميد ${target_freeze} بنجاح.${NC}"
        else
            echo -e "${RED}[!] تعذر تجميد الحزمة.${NC}"
        fi
    fi
else
    echo -e "${CYAN}تم التخطي بدون تجميد أي تطبيق.${NC}"
fi

# 5. تجميع الـ ART لتحسين سلاسة الواجهة والأنيميشن
echo -e "\n${CYAN}[4/5] إعادة تصريف كود التطبيقات بنمط speed-profile (AOT)...${NC}"
adb shell cmd package compile -m speed-profile -a
echo -e "${GREEN}✓ اكتمل تجميع الـ ART بنجاح.${NC}"

# 6. تجميع تطبيق محدد بنمط speed الكامل (اختياري)
echo ""
read -rp "هل ترغب بتجميع تطبيق محدد بنمط speed كامل؟ (y/N): " compile_custom
if [[ "$compile_custom" =~ ^[Yy]$ ]]; then
    read -rp "أدخل اسم الباكيج: " custom_pkg
    if [ -n "$custom_pkg" ]; then
        echo -e "${CYAN}جاري تجميع $custom_pkg بنمط speed...${NC}"
        adb shell cmd package compile -m speed -f "$custom_pkg"
        echo -e "${GREEN}✓ تم التجميع بأعلى أداء.${NC}"
    fi
fi

# التقرير النهائي وفحص الحرارة
echo -e "\n${CYAN}=== اكتملت العملية بنجاح ===${NC}"
FINAL_TEMP_RAW=$(adb shell dumpsys battery | grep temperature | awk '{print $2}' | tr -d '\r')
FINAL_TEMP_C=$(awk "BEGIN {print $FINAL_TEMP_RAW / 10}")
echo -e "${YELLOW}[i] حرارة البطارية الحالية: ${FINAL_TEMP_C}°C${NC}"
echo -e "${GREEN}[+] تم إنهاء الفحص والتحسين.${NC}"
