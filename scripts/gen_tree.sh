#!/usr/bin/env bash
# Строит docs/TREE.md: дерево репозитория (до 3 уровней папок) по git ls-files.
# Запуск после добавления/переименования файлов: scripts/gen_tree.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Описания папок. Ключ — путь без завершающего слеша.
# Неизвестные папки печатаются без описания — добавь сюда строку по мере роста.
declare -A DESC=(
  [ai]="клиент модели и промпты"
  [bot]="Telegram-бот и WhatsApp: обработчики, сервисы, запуск"
  [bot/handlers]="обработчики сообщений и кнопок Telegram"
  [bot/middlewares]="middleware aiogram: сервисы, ошибки, контекст"
  [bot/services]="бизнес-логика: диалог, заказы, каталог, WhatsApp"
  [db]="SQLite: подключение, миграции, репозитории"
  [db/migrations]="SQL-миграции схемы БД"
  [docs]="документация и планы"
  [docs/superpowers]="планы разработки"
  [docs/superpowers/plans]="планы по датам"
  [scripts]="вспомогательные скрипты"
  [tests]="тесты"
  [web]="панель заказов флориста (aiohttp)"
  [web/static]="статика панели (HTML/CSS/JS)"
  [.preview]="мокапы интерфейса"
  [.superpowers]="служебные артефакты superpowers (можно игнорировать)"
)

OUT="docs/TREE.md"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

# Собираем записи «путь<TAB>тип», тип d=папка, f=файл.
# Папки — только глубиной 1..3. Файлы — если лежат в папке глубиной ≤3.
git ls-files | sort | while IFS= read -r file; do
  dir="$file"
  while [[ "$dir" == */* ]]; do
    dir="${dir%/*}"
    depth=$(($(tr -cd '/' <<<"$dir" | wc -c) + 1))
    (( depth <= 3 )) && printf '%s\td\n' "$dir"
  done
  parent="${file%/*}"
  if [[ "$parent" == "$file" ]]; then
    printf '%s\tf\n' "$file"          # файл в корне
  else
    pdepth=$(($(tr -cd '/' <<<"$parent" | wc -c) + 1))
    (( pdepth <= 3 )) && printf '%s\tf\n' "$file"
  fi
done | sort -u > "$TMP"

# Рендерим дерево: отступ = 2 пробела на уровень, описание папок в строке.
{
  echo "# TREE.md — карта репозитория"
  echo
  echo "Автогенерировано: \`scripts/gen_tree.sh\` (не редактируй вручную)."
  echo "Папки до 3 уровней; у каждой папки — одна строка описания."
  echo
  echo '```'
  echo "."
  while IFS=$'\t' read -r path kind; do
    depth=$(($(tr -cd '/' <<<"$path" | wc -c) + 1))
    indent="$(printf '%*s' $((depth * 2)) '')"
    name="${path##*/}"
    if [[ "$kind" == "d" ]]; then
      desc="${DESC[$path]:-}"
      if [[ -n "$desc" ]]; then
        printf '%s%s/ — %s\n' "$indent" "$name" "$desc"
      else
        printf '%s%s/\n' "$indent" "$name"
      fi
    else
      printf '%s%s\n' "$indent" "$name"
    fi
  done < "$TMP"
  echo '```'
} > "$OUT"

echo "Готово: $OUT"
