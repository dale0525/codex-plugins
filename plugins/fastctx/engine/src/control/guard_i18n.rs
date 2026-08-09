//! Localized copy for provider-derived output protection.

use super::i18n::Language;

/// Complete provider-guard copy for one supported language.
#[derive(Debug)]
pub(crate) struct GuardMessages {
    pub(crate) section_title: &'static str,
    pub(crate) label: &'static str,
    pub(crate) active_note: &'static str,
    pub(crate) disabled_note: &'static str,
    pub(crate) available_note: &'static str,
    pub(crate) locked_note: &'static str,
    pub(crate) disable_confirm: &'static str,
    pub(crate) budget_follows_guarded_note: &'static str,
}

impl GuardMessages {
    #[cfg(test)]
    fn values(&self) -> [&'static str; 8] {
        [
            self.section_title,
            self.label,
            self.active_note,
            self.disabled_note,
            self.available_note,
            self.locked_note,
            self.disable_confirm,
            self.budget_follows_guarded_note,
        ]
    }
}

/// Returns provider-guard copy for a supported language.
pub(crate) const fn messages(language: Language) -> &'static GuardMessages {
    match language {
        Language::En => &EN,
        Language::ZhCn => &ZH_CN,
        Language::ZhTw => &ZH_TW,
        Language::Ja => &JA,
        Language::Ko => &KO,
        Language::Es => &ES,
        Language::Fr => &FR,
        Language::De => &DE,
        Language::PtBr => &PT_BR,
        Language::Ru => &RU,
        Language::It => &IT,
        Language::Tr => &TR,
        Language::Pl => &PL,
        Language::Nl => &NL,
        Language::Vi => &VI,
        Language::Id => &ID,
        Language::Uk => &UK,
    }
}

macro_rules! guard_messages {
    ($section:expr, $label:expr, $active:expr, $disabled:expr, $available:expr,
     $locked:expr, $confirm:expr, $budget:expr) => {
        GuardMessages {
            section_title: $section,
            label: $label,
            active_note: $active,
            disabled_note: $disabled,
            available_note: $available,
            locked_note: $locked,
            disable_confirm: $confirm,
            budget_follows_guarded_note: $budget,
        }
    };
}

const EN: GuardMessages = guard_messages!(
    "Provider compatibility",
    "Provider guard",
    "This provider compacts locally: Codex refeeds the whole history to the model, and one oversized tool output can make compaction fail. Guarded limits now cap each tool output for Apply and newly started runtime sessions.",
    "Protection is disabled. If your provider only compacts locally, one oversized tool output can make compaction fail.",
    "Protection is on but idle: no local-compaction provider is detected, so your selected tier stays in effect. Guarded limits engage automatically when one appears.",
    "Guarded is locked at host 10000 and FastCtx 9000. Your selected tier is preserved and returns automatically when remote compaction is available.",
    "Disable provider output protection?\nWithout Guarded limits, oversized tool outputs flow into local compaction — Codex refeeds the whole history to the model, and compaction can fail. This affects both Apply and newly started runtime sessions.",
    "auto — follows Guarded's full 100% share."
);
const ZH_CN: GuardMessages = guard_messages!(
    "Provider 兼容性",
    "Provider 输出保护",
    "当前 provider 不支持远端压缩，Codex 只能本地压缩：把整段历史重新交给模型，一份超大的工具输出可能导致压缩失败。已启用 Guarded 限制收紧单次工具输出，对 Apply 与新启动的运行时会话生效。",
    "保护已关闭。若 provider 只支持本地压缩，一份超大的工具输出就可能导致压缩失败。",
    "保护已开启，当前无需收紧：未检测到需要本地压缩的 provider，仍使用你选择的档位；一旦检测到会自动改用 Guarded 限制。",
    "Guarded 锁定为宿主 10000、FastCtx 9000。你选择的档位会保留，并在远端压缩可用时自动恢复。",
    "要关闭 provider 输出保护吗？\n没有 Guarded 限制时，超大的工具输出会直接进入本地压缩——Codex 要把整段历史重新交给模型，可能导致压缩失败。这会同时影响 Apply 和新启动的运行时会话。",
    "auto：跟随 Guarded 的完整 100% 比例。"
);
const ZH_TW: GuardMessages = guard_messages!(
    "Provider 相容性",
    "Provider 輸出保護",
    "目前的 provider 不支援遠端壓縮，Codex 只能本機壓縮：把整段歷史重新交給模型，一份過大的工具輸出可能導致壓縮失敗。已套用 Guarded 限制收緊單次工具輸出，對 Apply 與新啟動的執行階段工作階段生效。",
    "保護已停用。若 provider 只支援本機壓縮，一份過大的工具輸出就可能導致壓縮失敗。",
    "保護已啟用，目前無需收緊：未偵測到需要本機壓縮的 provider，仍使用所選層級；一旦偵測到會自動改用 Guarded 限制。",
    "Guarded 鎖定為主機 10000、FastCtx 9000。所選層級會保留，並在遠端壓縮可用時自動恢復。",
    "要停用 provider 輸出保護嗎？\n若沒有 Guarded 限制，過大的工具輸出會直接進入本機壓縮——Codex 要把整段歷史重新交給模型，可能導致壓縮失敗。這會同時影響 Apply 與新啟動的執行階段工作階段。",
    "auto：跟隨 Guarded 的完整 100% 比例。"
);
const JA: GuardMessages = guard_messages!(
    "Provider 互換性",
    "Provider 出力保護",
    "この provider はリモート圧縮に対応せず、Codex は履歴全体をモデルに渡し直すローカル圧縮しか使えません。巨大なツール出力があると圧縮が失敗することがあります。Guarded 制限で 1 回のツール出力を抑えており、Apply と新しく起動するランタイムセッションに適用されます。",
    "保護は無効です。provider がローカル圧縮しか使えない場合、巨大なツール出力ひとつで圧縮が失敗することがあります。",
    "保護は有効ですが現在は待機中です。ローカル圧縮が必要な provider は検出されていないため、選択した段階が有効です。検出されると自動で Guarded 制限に切り替わります。",
    "Guarded はホスト 10000、FastCtx 9000 に固定されます。選択した段階は保持され、リモート圧縮が使えると自動で戻ります。",
    "provider 出力保護を無効にしますか？\nGuarded 制限がないと、巨大なツール出力がそのままローカル圧縮に流れ込みます。Codex は履歴全体をモデルに渡し直すため、圧縮が失敗することがあります。Apply と新しいランタイムセッションの両方に影響します。",
    "auto：Guarded の完全な 100% 割合に従います。"
);
const KO: GuardMessages = guard_messages!(
    "Provider 호환성",
    "Provider 출력 보호",
    "이 provider는 원격 압축을 지원하지 않아 Codex는 전체 히스토리를 모델에 다시 전달하는 로컬 압축만 사용합니다. 거대한 도구 출력 하나가 압축을 실패시킬 수 있습니다. Guarded 제한이 도구 출력을 제한하며 Apply와 새로 시작하는 런타임 세션에 적용됩니다.",
    "보호가 꺼져 있습니다. provider가 로컬 압축만 지원한다면 거대한 도구 출력 하나가 압축을 실패시킬 수 있습니다.",
    "보호가 켜져 있지만 현재는 대기 중입니다. 로컬 압축이 필요한 provider가 감지되지 않아 선택한 단계가 유지됩니다. 감지되면 자동으로 Guarded 제한으로 전환됩니다.",
    "Guarded는 호스트 10000, FastCtx 9000으로 잠깁니다. 선택한 단계는 보존되며 원격 압축이 가능해지면 자동으로 복원됩니다.",
    "provider 출력 보호를 끌까요?\nGuarded 제한이 없으면 거대한 도구 출력이 그대로 로컬 압축에 들어갑니다. Codex는 전체 히스토리를 모델에 다시 전달하므로 압축이 실패할 수 있습니다. Apply와 새 런타임 세션 모두에 영향을 줍니다.",
    "auto: Guarded의 전체 100% 비율을 따릅니다."
);
const ES: GuardMessages = guard_messages!(
    "Compatibilidad del provider",
    "Protección del provider",
    "Este provider no admite compactación remota: Codex solo puede compactar localmente, reenviando todo el historial al modelo, y una salida de herramienta enorme puede hacer fallar la compactación. Los límites Guarded acotan cada salida de herramienta para Apply y las nuevas sesiones de ejecución.",
    "La protección está desactivada. Si el provider solo compacta localmente, una salida de herramienta enorme puede hacer fallar la compactación.",
    "La protección está activada pero inactiva: no se detecta un provider con compactación local, así que el nivel elegido sigue vigente. Los límites Guarded se aplican automáticamente cuando aparece uno.",
    "Guarded queda fijado en 10000 para el host y 9000 para FastCtx. El nivel elegido se conserva y vuelve automáticamente cuando hay compactación remota.",
    "¿Desactivar la protección de salida del provider?\nSin los límites Guarded, las salidas enormes entran directas a la compactación local: Codex reenvía todo el historial al modelo y la compactación puede fallar. Afecta a Apply y a las nuevas sesiones de ejecución.",
    "auto — sigue la proporción completa del 100% de Guarded."
);
const FR: GuardMessages = guard_messages!(
    "Compatibilité du provider",
    "Protection du provider",
    "Ce provider n’offre pas de compactage distant : Codex ne peut compacter que localement, en renvoyant tout l’historique au modèle, et une sortie d’outil énorme peut faire échouer le compactage. Les limites Guarded plafonnent chaque sortie d’outil pour Apply et les nouvelles sessions d’exécution.",
    "La protection est désactivée. Si le provider ne compacte que localement, une seule sortie d’outil énorme peut faire échouer le compactage.",
    "La protection est activée mais en veille : aucun provider à compactage local n’est détecté, le niveau choisi reste donc en vigueur. Les limites Guarded s’appliquent automatiquement dès qu’un tel provider apparaît.",
    "Guarded est verrouillé à 10000 côté hôte et 9000 côté FastCtx. Le niveau choisi est conservé et revient automatiquement quand le compactage distant est disponible.",
    "Désactiver la protection de sortie du provider ?\nSans les limites Guarded, les sorties énormes partent telles quelles dans le compactage local : Codex renvoie tout l’historique au modèle et le compactage peut échouer. Cela touche Apply et les nouvelles sessions d’exécution.",
    "auto — suit la part complète de 100 % de Guarded."
);
const DE: GuardMessages = guard_messages!(
    "Provider-Kompatibilität",
    "Provider-Schutz",
    "Dieser Provider bietet keine Remote-Komprimierung: Codex kann nur lokal komprimieren und reicht dazu den gesamten Verlauf erneut an das Modell, und eine riesige Tool-Ausgabe kann die Komprimierung scheitern lassen. Guarded-Grenzen deckeln jede Tool-Ausgabe für Apply und neu gestartete Laufzeitsitzungen.",
    "Der Schutz ist deaktiviert. Komprimiert der Provider nur lokal, kann eine einzige riesige Tool-Ausgabe die Komprimierung scheitern lassen.",
    "Der Schutz ist aktiviert, aber derzeit unbeteiligt: Es ist kein Provider mit lokaler Komprimierung erkannt, die gewählte Stufe bleibt wirksam. Guarded-Grenzen greifen automatisch, sobald einer erkannt wird.",
    "Guarded ist auf Host 10000 und FastCtx 9000 gesperrt. Die gewählte Stufe bleibt erhalten und kehrt bei verfügbarer Remote-Komprimierung automatisch zurück.",
    "Provider-Ausgabeschutz deaktivieren?\nOhne Guarded-Grenzen fließen riesige Tool-Ausgaben direkt in die lokale Komprimierung: Codex reicht den gesamten Verlauf erneut an das Modell, und die Komprimierung kann scheitern. Dies betrifft Apply und neue Laufzeitsitzungen.",
    "auto — folgt dem vollen Guarded-Anteil von 100 %."
);
const PT_BR: GuardMessages = guard_messages!(
    "Compatibilidade do provider",
    "Proteção do provider",
    "Este provider não tem compactação remota: o Codex só compacta localmente, reenviando todo o histórico ao modelo, e uma saída de ferramenta enorme pode fazer a compactação falhar. Os limites Guarded restringem cada saída de ferramenta para Apply e novas sessões de execução.",
    "A proteção está desativada. Se o provider só compacta localmente, uma única saída de ferramenta enorme pode fazer a compactação falhar.",
    "A proteção está ativada, mas ociosa: nenhum provider com compactação local foi detectado, então o nível escolhido continua valendo. Os limites Guarded entram automaticamente quando um for detectado.",
    "Guarded fica travado em 10000 para o host e 9000 para o FastCtx. O nível escolhido é preservado e retorna automaticamente quando há compactação remota.",
    "Desativar a proteção de saída do provider?\nSem os limites Guarded, saídas enormes entram direto na compactação local: o Codex reenvia todo o histórico ao modelo e a compactação pode falhar. Isso afeta Apply e novas sessões de execução.",
    "auto — segue a fração completa de 100% do Guarded."
);
const RU: GuardMessages = guard_messages!(
    "Совместимость provider",
    "Защита provider",
    "Этот provider не поддерживает удалённое сжатие: Codex сжимает только локально, заново передавая модели всю историю, и один огромный вывод инструмента может сорвать сжатие. Ограничения Guarded сдерживают каждый вывод инструмента; действует для Apply и новых сеансов выполнения.",
    "Защита отключена. Если provider сжимает только локально, один огромный вывод инструмента может сорвать сжатие.",
    "Защита включена, но сейчас бездействует: provider с локальным сжатием не обнаружен, действует выбранный уровень. Ограничения Guarded включатся автоматически при его обнаружении.",
    "Guarded фиксирует пределы 10000 для хоста и 9000 для FastCtx. Выбранный уровень сохраняется и автоматически возвращается при доступном удалённом сжатии.",
    "Отключить защиту вывода provider?\nБез ограничений Guarded огромный вывод инструментов попадает прямо в локальное сжатие: Codex заново передаёт модели всю историю, и сжатие может сорваться. Это влияет на Apply и новые сеансы выполнения.",
    "auto — следует полной доле Guarded 100%."
);
const IT: GuardMessages = guard_messages!(
    "Compatibilità provider",
    "Protezione provider",
    "Questo provider non ha compattazione remota: Codex può compattare solo localmente, rimandando l’intera cronologia al modello, e un output di strumento enorme può far fallire la compattazione. I limiti Guarded contengono ogni output di strumento per Apply e per le nuove sessioni di esecuzione.",
    "La protezione è disattivata. Se il provider compatta solo localmente, un solo output di strumento enorme può far fallire la compattazione.",
    "La protezione è attiva ma a riposo: nessun provider con compattazione locale è stato rilevato, quindi resta valido il livello scelto. I limiti Guarded scattano automaticamente appena ne viene rilevato uno.",
    "Guarded è bloccato a 10000 per l’host e 9000 per FastCtx. Il livello scelto viene conservato e torna automaticamente quando è disponibile la compattazione remota.",
    "Disattivare la protezione output del provider?\nSenza i limiti Guarded, gli output enormi finiscono dritti nella compattazione locale: Codex rimanda l’intera cronologia al modello e la compattazione può fallire. Riguarda Apply e le nuove sessioni di esecuzione.",
    "auto — segue la quota completa del 100% di Guarded."
);
const TR: GuardMessages = guard_messages!(
    "Provider uyumluluğu",
    "Provider koruması",
    "Bu provider uzaktan sıkıştırmayı desteklemiyor: Codex yalnızca yerel sıkıştırma yapabilir ve tüm geçmişi modele yeniden gönderir; devasa bir araç çıktısı sıkıştırmayı başarısız kılabilir. Guarded sınırları her araç çıktısını sınırlar; Apply ve yeni başlatılan çalışma zamanı oturumlarında geçerlidir.",
    "Koruma kapalı. Provider yalnızca yerel sıkıştırma yapıyorsa, tek bir devasa araç çıktısı sıkıştırmayı başarısız kılabilir.",
    "Koruma açık ama şu an devrede değil: yerel sıkıştırma gerektiren bir provider algılanmadı, seçilen kademe geçerli. Algılandığında Guarded sınırları otomatik devreye girer.",
    "Guarded, host için 10000 ve FastCtx için 9000 değerine kilitlenir. Seçilen kademe korunur ve uzaktan sıkıştırma kullanılabildiğinde otomatik döner.",
    "Provider çıktı koruması kapatılsın mı?\nGuarded sınırları olmadan devasa araç çıktıları doğrudan yerel sıkıştırmaya girer: Codex tüm geçmişi modele yeniden gönderir ve sıkıştırma başarısız olabilir. Apply ve yeni çalışma zamanı oturumlarını etkiler.",
    "auto — Guarded'ın tam %100 payını izler."
);
const PL: GuardMessages = guard_messages!(
    "Zgodność providera",
    "Ochrona providera",
    "Ten provider nie ma zdalnej kompakcji: Codex kompaktuje tylko lokalnie, przekazując modelowi ponownie całą historię, a jedno ogromne wyjście narzędzia może sprawić, że kompakcja zawiedzie. Limity Guarded ograniczają każde wyjście narzędzia; obowiązują dla Apply i nowych sesji wykonawczych.",
    "Ochrona jest wyłączona. Jeśli provider kompaktuje tylko lokalnie, jedno ogromne wyjście narzędzia może sprawić, że kompakcja zawiedzie.",
    "Ochrona jest włączona, ale bezczynna: nie wykryto providera z lokalną kompakcją, więc obowiązuje wybrany poziom. Limity Guarded włączą się automatycznie po jego wykryciu.",
    "Guarded jest zablokowany na 10000 dla hosta i 9000 dla FastCtx. Wybrany poziom zostaje zachowany i wraca automatycznie, gdy dostępna jest kompakcja zdalna.",
    "Wyłączyć ochronę wyjścia providera?\nBez limitów Guarded ogromne wyjścia narzędzi trafiają wprost do lokalnej kompakcji: Codex przekazuje modelowi ponownie całą historię i kompakcja może zawieść. Dotyczy to Apply i nowych sesji wykonawczych.",
    "auto — podąża za pełnym udziałem Guarded 100%."
);
const NL: GuardMessages = guard_messages!(
    "Providercompatibiliteit",
    "Providerbescherming",
    "Deze provider heeft geen externe compactie: Codex kan alleen lokaal compacteren en voert daarbij de hele geschiedenis opnieuw aan het model, en één enorme tooluitvoer kan de compactie laten mislukken. Guarded-limieten begrenzen elke tooluitvoer voor Apply en nieuw gestarte runtimesessies.",
    "Bescherming is uitgeschakeld. Als de provider alleen lokaal compacteert, kan één enorme tooluitvoer de compactie laten mislukken.",
    "Bescherming is ingeschakeld maar rust: er is geen provider met lokale compactie gedetecteerd, dus het gekozen niveau blijft gelden. Guarded-limieten treden automatisch in werking zodra er een wordt gedetecteerd.",
    "Guarded is vergrendeld op host 10000 en FastCtx 9000. Het gekozen niveau blijft bewaard en keert automatisch terug zodra externe compactie beschikbaar is.",
    "Provider-uitvoerbescherming uitschakelen?\nZonder Guarded-limieten stroomt enorme tooluitvoer rechtstreeks de lokale compactie in: Codex voert de hele geschiedenis opnieuw aan het model en de compactie kan mislukken. Dit raakt Apply en nieuwe runtimesessies.",
    "auto — volgt het volledige Guarded-aandeel van 100%."
);
const VI: GuardMessages = guard_messages!(
    "Tương thích provider",
    "Bảo vệ provider",
    "Provider này không hỗ trợ nén từ xa: Codex chỉ có thể nén cục bộ bằng cách đưa lại toàn bộ lịch sử cho mô hình, và một đầu ra công cụ khổng lồ có thể khiến việc nén thất bại. Giới hạn Guarded đang chặn từng đầu ra công cụ; áp dụng cho Apply và các phiên chạy mới.",
    "Bảo vệ đã tắt. Nếu provider chỉ nén cục bộ, một đầu ra công cụ khổng lồ có thể khiến việc nén thất bại.",
    "Bảo vệ đang bật nhưng chưa cần dùng: chưa phát hiện provider cần nén cục bộ nên mức bạn chọn vẫn có hiệu lực. Giới hạn Guarded sẽ tự kích hoạt khi phát hiện.",
    "Guarded khóa ở 10000 cho host và 9000 cho FastCtx. Mức đã chọn được giữ lại và tự động trở lại khi có nén từ xa.",
    "Tắt bảo vệ đầu ra provider?\nKhông có giới hạn Guarded, đầu ra công cụ khổng lồ sẽ đi thẳng vào nén cục bộ: Codex đưa lại toàn bộ lịch sử cho mô hình và việc nén có thể thất bại. Ảnh hưởng đến Apply và các phiên chạy mới.",
    "auto — theo tỷ lệ đầy đủ 100% của Guarded."
);
const ID: GuardMessages = guard_messages!(
    "Kompatibilitas provider",
    "Perlindungan provider",
    "Provider ini tidak mendukung kompaksi jarak jauh: Codex hanya bisa kompaksi lokal dengan mengirim ulang seluruh riwayat ke model, dan satu keluaran alat yang sangat besar dapat membuat kompaksi gagal. Batas Guarded membatasi setiap keluaran alat; berlaku untuk Apply dan sesi runtime yang baru dimulai.",
    "Perlindungan dinonaktifkan. Jika provider hanya kompaksi lokal, satu keluaran alat yang sangat besar dapat membuat kompaksi gagal.",
    "Perlindungan aktif tetapi belum diperlukan: tidak terdeteksi provider yang butuh kompaksi lokal, jadi tingkat pilihan tetap berlaku. Batas Guarded aktif otomatis saat terdeteksi.",
    "Guarded dikunci pada host 10000 dan FastCtx 9000. Tingkat pilihan disimpan dan kembali otomatis saat kompaksi jarak jauh tersedia.",
    "Nonaktifkan perlindungan keluaran provider?\nTanpa batas Guarded, keluaran alat yang sangat besar masuk langsung ke kompaksi lokal: Codex mengirim ulang seluruh riwayat ke model dan kompaksi bisa gagal. Ini memengaruhi Apply dan sesi runtime baru.",
    "auto — mengikuti porsi penuh 100% Guarded."
);
const UK: GuardMessages = guard_messages!(
    "Сумісність provider",
    "Захист provider",
    "Цей provider не підтримує віддалене стиснення: Codex стискає лише локально, заново передаючи моделі всю історію, і один величезний вивід інструмента може зірвати стиснення. Обмеження Guarded стримують кожен вивід інструмента; діє для Apply і нових сеансів виконання.",
    "Захист вимкнено. Якщо provider стискає лише локально, один величезний вивід інструмента може зірвати стиснення.",
    "Захист увімкнено, але наразі бездіяльний: provider з локальним стисненням не виявлено, тож діє вибраний рівень. Обмеження Guarded увімкнуться автоматично після виявлення.",
    "Guarded фіксує 10000 для хоста і 9000 для FastCtx. Вибраний рівень зберігається й автоматично повертається, коли доступне віддалене стиснення.",
    "Вимкнути захист виводу provider?\nБез обмежень Guarded величезний вивід інструментів потрапляє прямо в локальне стиснення: Codex заново передає моделі всю історію, і стиснення може зірватися. Це впливає на Apply і нові сеанси виконання.",
    "auto — дотримується повної частки Guarded 100%."
);

#[cfg(test)]
mod tests {
    use super::messages;
    use crate::control::i18n::ALL_LANGUAGES;

    #[test]
    fn every_language_has_complete_provider_guard_copy() {
        for language in ALL_LANGUAGES {
            let messages = messages(language);
            assert!(
                messages
                    .values()
                    .iter()
                    .all(|value| !value.trim().is_empty()),
                "{} has an empty provider-guard translation",
                language.code()
            );
            // The section header sits directly above the item row, so identical text
            // renders as a duplicated line rather than a heading and its setting.
            assert_ne!(
                messages.section_title,
                messages.label,
                "{} repeats the provider-guard label as its section header",
                language.code()
            );
            assert!(messages.locked_note.contains("10000"));
            assert!(messages.locked_note.contains("9000"));
            assert!(messages.disable_confirm.contains("Guarded"));
            assert!(messages.budget_follows_guarded_note.contains("100"));
            assert!(messages.budget_follows_guarded_note.contains('%'));
        }
    }
}
