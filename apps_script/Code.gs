function doGet(e) {
  const sh = SpreadsheetApp.getActiveSheet();
  if (e.parameter.member) {                    // 값이 붙어 오면 → 시트에 저장
    const now = Utilities.formatDate(new Date(), "Asia/Seoul", "yyyy-MM-dd HH:mm:ss");
    sh.appendRow(["'" + now, plain(e.parameter.member), plain(e.parameter.menu), plain(e.parameter.type)]);  // 작은따옴표 = 시트가 날짜로 바꾸지 못하게 문자 그대로
    return ContentService.createTextOutput("OK");
  }
  const rows = sh.getDataRange().getValues();  // 안 붙어 오면 → 전체 기록 돌려주기
  return ContentService.createTextOutput(JSON.stringify(rows))
                       .setMimeType(ContentService.MimeType.JSON);
}

// 공개 주소로 들어온 글을 문자로 고정한다. =로 시작하는 글이 수식으로 실행되지 않게 하고, 길이를 제한한다.
function plain(value) {
  const text = String(value || "").slice(0, 100);
  return /^[=+\-@]/.test(text) ? "'" + text : text;
}
